import os
import io
import sqlite3
import tempfile
import unittest
from datetime import datetime
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from backend import analisis_territorial as analisis
from backend.configuracion import ROOT, cargar_entorno
from backend.territorio import Territorio


MAPA = {k: k for k in ("anio", "entidad", "municipio", "seccion", "id_distrito_local",
                       "id_distrito_federal", "tipo_eleccion", "partido", "votos")}


class PerfilV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with sqlite3.connect(ROOT / "data" / "electoral.db") as db:
            cls.df = pd.read_sql_query("SELECT * FROM resultados", db)
        cls.t = Territorio(cls.df, MAPA)

    def test_comparativo_coincide_con_perfil_en_cada_eleccion(self):
        for nivel, unidad in (("municipio", "SAN LUIS POTOSI"), ("entidad", str(self.df["entidad"].dropna().iloc[0])),
                              ("id_distrito_local", "8"), ("id_distrito_federal", "2"), ("seccion", "1")):
            with self.subTest(nivel=nivel):
                perfil = self.t.perfil(nivel, unidad, "2024")
                comparativo = analisis.comparativo(self.t, nivel, unidad, "2024")
                for e in perfil["elecciones"]:
                    otra = next(x for x in comparativo["elecciones"] if x["tipo"] == e["tipo"])
                    self.assertEqual(otra["votos"], {r["partido"]: r["votos"] for r in e["ranking"]})
                    self.assertEqual(otra["pct"], {r["partido"]: r["porcentaje"] for r in e["ranking"]})

    def test_aliados_y_coaliciones_no_se_suman(self):
        df = pd.DataFrame({"norm": ["PAN", "PRI", "PAN_PRI", "MORENA", "PVEM", "PT"],
                           "votos": [100, 80, 30, 90, 60, 20]})
        resumen = analisis._resumen(df)
        self.assertEqual(resumen["votos"], dict(zip(df.norm, df.votos)))
        self.assertEqual(resumen["ganador"], "PAN")
        self.assertAlmostEqual(sum(resumen["pct"].values()), 100, places=1)

    def test_auxiliares_y_nulos_no_son_partidos(self):
        df = pd.DataFrame({"norm": ["PAN", "PRI", "NULOS", "NO_REGISTRADAS", "LISTA_NOMINAL", "TOTAL_VOTOS_CALCULADOS"],
                           "votos": [100, 80, 15, 5, 250, 200]})
        r = analisis._resumen(df)
        self.assertEqual(r["votos"], {"PAN": 100, "PRI": 80})
        self.assertEqual(r["pct"]["PAN"], 50)
        self.assertEqual(r["participacion_pct"], 80)

    def test_opciones_desconocidas_se_conservan(self):
        r = analisis._resumen(pd.DataFrame({"norm": ["NUEVO", "NA"], "votos": [150, 100]}))
        self.assertEqual(r["votos"], {"NA": 100, "NUEVO": 150})

    def test_ficha_y_contrato_son_por_partido(self):
        r = analisis.comparativo(self.t, "municipio", "SAN LUIS POTOSI", "2024")
        self.assertIn("partidos", r)
        self.assertNotIn("bloques", r)
        self.assertTrue(all("por_partido" in d for d in r["diferencias"]))
        ficha = analisis.construir_ficha(r)
        self.assertTrue(any("no se agrupan en bloques" in l for l in ficha["lineas"]))
        self.assertFalse(any("MORENA_ALIADOS" in l for l in ficha["lineas"]))
        self.assertIsNone(r["historico"])

    def test_anio_inexistente_no_mezcla_datos(self):
        with self.assertRaises(ValueError):
            analisis.comparativo(self.t, "municipio", "SAN LUIS POTOSI", "2021")

    def test_redaccion_recibe_ficha_por_opcion_y_cache_funciona(self):
        llamadas = []
        def crear(**kwargs):
            llamadas.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="La ficha presenta resultados por opción registrada."))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=crear)))
        analisis._CACHE.clear()
        r = analisis.analizar(self.t, client, "modelo-prueba", "municipio", "SAN LUIS POTOSI", "2024")
        self.assertIsNotNone(datetime.fromisoformat(r["generado"]).tzinfo)
        self.assertIn("no se agrupan en bloques", llamadas[0]["messages"][1]["content"])
        self.assertTrue(analisis.analizar(self.t, client, "modelo-prueba", "municipio", "SAN LUIS POTOSI", "2024")["en_cache"])
        self.assertEqual(len(llamadas), 1)
        analisis.analizar(self.t, client, "modelo-prueba", "municipio", "SAN LUIS POTOSI", "2024", regenerar=True)
        self.assertEqual(len(llamadas), 2)

    def test_env_se_carga_desde_raiz_y_respeta_servidor(self):
        with tempfile.TemporaryDirectory() as carpeta, patch.dict(os.environ, {}, clear=True):
            raiz = Path(carpeta)
            (raiz / ".env").write_text("GROQ_API_KEY=clave-prueba\n", encoding="utf-8")
            self.assertTrue(cargar_entorno(raiz))
            self.assertEqual(os.environ["GROQ_API_KEY"], "clave-prueba")
            os.environ["GROQ_API_KEY"] = "clave-servidor"
            cargar_entorno(raiz)
            self.assertEqual(os.environ["GROQ_API_KEY"], "clave-servidor")

    def test_env_example_no_es_configuracion_activa(self):
        with tempfile.TemporaryDirectory() as carpeta, patch.dict(os.environ, {}, clear=True):
            raiz = Path(carpeta)
            (raiz / ".env.example").write_text("GROQ_API_KEY=ejemplo\n", encoding="utf-8")
            self.assertFalse(cargar_entorno(raiz))
            self.assertNotIn("GROQ_API_KEY", os.environ)

    def test_variable_vacia_no_oculta_clave_local(self):
        with tempfile.TemporaryDirectory() as carpeta, patch.dict(os.environ, {"GROQ_API_KEY": " "}, clear=True):
            raiz = Path(carpeta)
            (raiz / ".env").write_text("GROQ_API_KEY=clave-local\n", encoding="utf-8")
            cargar_entorno(raiz)
            self.assertEqual(os.environ["GROQ_API_KEY"], "clave-local")

    def test_recupera_configuracion_antigua(self):
        with tempfile.TemporaryDirectory() as carpeta, patch.dict(os.environ, {}, clear=True):
            raiz = Path(carpeta)
            clave = "gsk_" + "prueba" * 6
            (raiz / ".env.example").write_text(f"GROQ_API_KEY={clave}\n", encoding="utf-8")
            self.assertTrue(cargar_entorno(raiz))
            self.assertEqual(os.environ["GROQ_API_KEY"], clave)

    def test_configurador_conserva_variables_y_no_imprime_clave(self):
        import configurar_claves
        with tempfile.TemporaryDirectory() as carpeta, patch.dict(os.environ, {}, clear=True):
            raiz = Path(carpeta)
            ruta = raiz / ".env"
            ruta.write_text("GROQ_MODEL=modelo-local\nSERPAPI_KEY=otra-clave\n", encoding="utf-8")
            salida = io.StringIO()
            with patch.object(configurar_claves, "ROOT", raiz), patch.object(configurar_claves, "cargar_entorno"), \
                    patch.object(configurar_claves, "getpass", return_value="clave-introducida"), redirect_stdout(salida):
                self.assertEqual(configurar_claves.main(), 0)
            from dotenv import dotenv_values
            valores = dotenv_values(ruta)
            self.assertEqual(valores["GROQ_API_KEY"], "clave-introducida")
            self.assertEqual(valores["GROQ_MODEL"], "modelo-local")
            self.assertEqual(valores["SERPAPI_KEY"], "otra-clave")
            self.assertNotIn("clave-introducida", salida.getvalue())


if __name__ == "__main__":
    unittest.main()
