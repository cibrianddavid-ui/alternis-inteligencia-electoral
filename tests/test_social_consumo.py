"""Pruebas unitarias para el contador y control de consumo de Apify por usuario.

Valida:
1. Registro aislado de consumo por usuario (aislamiento multi-usuario).
2. Filtros temporales (hoy, semana, mes, todo).
3. Fecha de inicio de conteo.
4. Vista global exclusiva para administradores.
5. Regla de costo cero: consultas a BD local / caché no incrementan el contador.
6. Endpoint API GET /api/social/consumo con autenticación y permisos de rol.
"""

import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend import auth, basedatos
from backend.main import app
from backend.social.db import (
    asegurar_esquema_social,
    db_registrar_consumo_apify,
    db_obtener_resumen_consumo,
    db_verificar_cuota_scrapeo,
)
from backend.social.models import CuotaScrapingExcedidaError, SolicitudSocial
from backend.social.scraping import _obtener_posts, _obtener_comentarios


class SocialConsumoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        asegurar_esquema_social()

    def setUp(self):
        # Limpiar tabla de consumo para pruebas limpias
        with basedatos.transaccion() as conn:
            conn.execute("DELETE FROM social_apify_consumo WHERE usuario LIKE 'test_%'")

    def tearDown(self):
        # Limpiar registros creados durante las pruebas
        with basedatos.transaccion() as conn:
            conn.execute("DELETE FROM social_apify_consumo WHERE usuario LIKE 'test_%'")
        app.dependency_overrides.clear()

    def test_aislamiento_consumo_por_usuario(self):
        """El consumo de un usuario no debe mezclarse con el de otro."""
        usuario_a = "test_maria"
        usuario_b = "test_carlos"

        # Registrar consumo para Maria
        db_registrar_consumo_apify(
            usuario=usuario_a,
            tipo="perfil",
            actor="apify/facebook-posts-scraper",
            url_objetivo="https://facebook.com/paginaA",
            posts_scrapeados=5,
            comentarios_scrapeados=20,
            run_id="run_maria_1",
        )

        # Registrar consumo para Carlos
        db_registrar_consumo_apify(
            usuario=usuario_b,
            tipo="posts",
            actor="apify/facebook-comments-scraper",
            url_objetivo="https://facebook.com/postB",
            posts_scrapeados=2,
            comentarios_scrapeados=50,
            run_id="run_carlos_1",
        )

        # Consultar consumo de Maria
        resumen_maria = db_obtener_resumen_consumo(usuario_a, periodo="todo")
        self.assertEqual(resumen_maria["usuario"], usuario_a)
        self.assertFalse(resumen_maria["es_global"])
        self.assertEqual(resumen_maria["posts_scrapeados"], 5)
        self.assertEqual(resumen_maria["comentarios_scrapeados"], 20)
        self.assertEqual(resumen_maria["llamadas_apify"], 1)
        # 5 * 0.005 + 20 * 0.0005 = 0.025 + 0.010 = 0.035 USD
        self.assertAlmostEqual(resumen_maria["costo_estimado_usd"], 0.035, places=4)

        # Consultar consumo de Carlos
        resumen_carlos = db_obtener_resumen_consumo(usuario_b, periodo="todo")
        self.assertEqual(resumen_carlos["usuario"], usuario_b)
        self.assertFalse(resumen_carlos["es_global"])
        self.assertEqual(resumen_carlos["posts_scrapeados"], 2)
        self.assertEqual(resumen_carlos["comentarios_scrapeados"], 50)
        self.assertEqual(resumen_carlos["llamadas_apify"], 1)
        # 2 * 0.005 + 50 * 0.0005 = 0.010 + 0.025 = 0.035 USD
        self.assertAlmostEqual(resumen_carlos["costo_estimado_usd"], 0.035, places=4)

    def test_filtros_temporales(self):
        """Los filtros 'hoy', 'semana', 'mes', 'todo' deben acotar correctamente los registros."""
        usuario = "test_temporal"
        # Fijamos una fecha de referencia: 15 de noviembre de 2026 a las 12:00 UTC
        ahora_fijo = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)

        # 1. Hoy (15 nov)
        ts_hoy = ahora_fijo.isoformat()
        # 2. Hace 3 días (12 nov: esta semana y este mes)
        ts_semana = (ahora_fijo - timedelta(days=3)).isoformat()
        # 3. Hace 11 días (4 nov: este mes, pero fuera de la semana de 7 días)
        ts_mes = (ahora_fijo - timedelta(days=11)).isoformat()
        # 4. Hace 45 días (1 oct: mes anterior, solo en 'todo')
        ts_antiguo = (ahora_fijo - timedelta(days=45)).isoformat()

        with basedatos.transaccion() as conn:
            conn.execute(
                """
                INSERT INTO social_apify_consumo
                (timestamp, usuario, tipo, actor, url_objetivo, posts_scrapeados, comentarios_scrapeados, run_id)
                VALUES (?, ?, 'perfil', 'apify', 'url', ?, ?, 'run')
                """,
                (ts_hoy, usuario, 1, 10),
            )
            conn.execute(
                """
                INSERT INTO social_apify_consumo
                (timestamp, usuario, tipo, actor, url_objetivo, posts_scrapeados, comentarios_scrapeados, run_id)
                VALUES (?, ?, 'perfil', 'apify', 'url', ?, ?, 'run')
                """,
                (ts_semana, usuario, 2, 20),
            )
            conn.execute(
                """
                INSERT INTO social_apify_consumo
                (timestamp, usuario, tipo, actor, url_objetivo, posts_scrapeados, comentarios_scrapeados, run_id)
                VALUES (?, ?, 'perfil', 'apify', 'url', ?, ?, 'run')
                """,
                (ts_mes, usuario, 4, 40),
            )
            conn.execute(
                """
                INSERT INTO social_apify_consumo
                (timestamp, usuario, tipo, actor, url_objetivo, posts_scrapeados, comentarios_scrapeados, run_id)
                VALUES (?, ?, 'perfil', 'apify', 'url', ?, ?, 'run')
                """,
                (ts_antiguo, usuario, 8, 80),
            )

        class MockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return ahora_fijo

        with patch("backend.social.db.datetime", MockDatetime):
            # Filtro hoy: solo el de hoy
            res_hoy = db_obtener_resumen_consumo(usuario, periodo="hoy")
            self.assertEqual(res_hoy["posts_scrapeados"], 1)
            self.assertEqual(res_hoy["comentarios_scrapeados"], 10)
            self.assertEqual(res_hoy["llamadas_apify"], 1)

            # Filtro semana: hoy + hace 3 días
            res_semana = db_obtener_resumen_consumo(usuario, periodo="semana")
            self.assertEqual(res_semana["posts_scrapeados"], 1 + 2)
            self.assertEqual(res_semana["comentarios_scrapeados"], 10 + 20)
            self.assertEqual(res_semana["llamadas_apify"], 2)

            # Filtro mes: hoy + 3 días + 11 días
            res_mes = db_obtener_resumen_consumo(usuario, periodo="mes")
            self.assertEqual(res_mes["posts_scrapeados"], 1 + 2 + 4)
            self.assertEqual(res_mes["comentarios_scrapeados"], 10 + 20 + 40)
            self.assertEqual(res_mes["llamadas_apify"], 3)

            # Filtro todo: todos los 4 registros
            res_todo = db_obtener_resumen_consumo(usuario, periodo="todo")
            self.assertEqual(res_todo["posts_scrapeados"], 1 + 2 + 4 + 8)
            self.assertEqual(res_todo["comentarios_scrapeados"], 10 + 20 + 40 + 80)
            self.assertEqual(res_todo["llamadas_apify"], 4)

    def test_fecha_inicio_conteo(self):
        """La fecha de inicio debe corresponder a la fecha de la primera llamada registrada."""
        usuario = "test_fecha"
        ts_antiguo = "2026-05-10T12:00:00+00:00"
        ts_reciente = "2026-08-20T12:00:00+00:00"

        with basedatos.transaccion() as conn:
            conn.execute(
                """
                INSERT INTO social_apify_consumo
                (timestamp, usuario, tipo, actor, url_objetivo, posts_scrapeados, comentarios_scrapeados, run_id)
                VALUES (?, ?, 'perfil', 'apify', 'url', 1, 5, 'run1'),
                       (?, ?, 'perfil', 'apify', 'url', 2, 10, 'run2')
                """,
                (ts_antiguo, usuario, ts_reciente, usuario),
            )

        resumen = db_obtener_resumen_consumo(usuario, periodo="mes")
        self.assertEqual(resumen["fecha_inicio_conteo"], "2026-05-10")

    def test_vista_global_acumula_todos(self):
        """La vista global acumula el consumo de todos los usuarios."""
        u1 = "test_user1"
        u2 = "test_user2"

        db_registrar_consumo_apify(u1, "perfil", "actor", "url1", 10, 100, "run1")
        db_registrar_consumo_apify(u2, "posts", "actor", "url2", 20, 200, "run2")

        resumen_global = db_obtener_resumen_consumo("cualquiera", periodo="todo", ver_global=True)
        self.assertTrue(resumen_global["es_global"])
        self.assertGreaterEqual(resumen_global["posts_scrapeados"], 30)
        self.assertGreaterEqual(resumen_global["comentarios_scrapeados"], 300)
        self.assertGreaterEqual(resumen_global["llamadas_apify"], 2)

    def test_cero_costo_en_cache_y_bd(self):
        """Consultas al módulo histórico y lecturas desde la BD local tienen costo $0 y no suman al contador."""
        from backend.social.historico import analizar_historico_perfil

        # Verificar contador inicial
        usuario = "test_nocost"
        res_inicio = db_obtener_resumen_consumo(usuario, periodo="todo")
        self.assertEqual(res_inicio["posts_scrapeados"], 0)
        self.assertEqual(res_inicio["comentarios_scrapeados"], 0)

        with patch("backend.social.db.db_registrar_consumo_apify") as mock_registrar:
            # Ejecutar análisis del módulo histórico (opera 100% en BD local con 0 llamadas a Apify)
            analizar_historico_perfil(url_perfil="https://facebook.com/fake_page", agrupacion="mes")
            # Debe garantizar costo $0: ninguna llamada a registrar consumo Apify
            mock_registrar.assert_not_called()

        # El contador sigue en cero
        res_fin = db_obtener_resumen_consumo(usuario, periodo="todo")
        self.assertEqual(res_fin["posts_scrapeados"], 0)
        self.assertEqual(res_fin["comentarios_scrapeados"], 0)

    def test_cero_costo_reconsulta_perfil_en_bd(self):
        """Re-consultar un perfil recientemente sincronizado no debe llamar a Apify ni incrementar contadores."""
        from backend.social.models import SolicitudSocial, ResultadoSocial, ANALISIS_VERSION
        from backend.social.pipeline import analizar_perfil
        from backend.social.db import db_guardar_posts, db_guardar_comentarios, db_marcar_post_refrescado, db_guardar_cache, _fingerprint_dataset

        test_perfil = "https://facebook.com/test_perfil_cache"
        post1_url = "https://facebook.com/test_perfil_cache/posts/101"
        post2_url = "https://facebook.com/test_perfil_cache/posts/102"

        # 1. Sembrar posts en BD
        db_guardar_posts(test_perfil, [
            {"url": post1_url, "texto": "Post 1 de prueba", "fecha": "2026-11-09T10:00:00Z", "likes": 5},
            {"url": post2_url, "texto": "Post 2 de prueba", "fecha": "2026-11-09T09:00:00Z", "likes": 10},
        ])
        db_marcar_post_refrescado(post1_url, max_comentarios=50)
        db_marcar_post_refrescado(post2_url, max_comentarios=50)

        # 2. Sembrar comentarios clasificados
        comms = [
            {"id": "c1", "post_url": post1_url, "texto": "Buen trabajo diputado", "tono": "positivo", "analizado": 1, "analisis_version": ANALISIS_VERSION},
            {"id": "c2", "post_url": post2_url, "texto": "No estoy de acuerdo con esto", "tono": "negativo", "analizado": 1, "analisis_version": ANALISIS_VERSION},
        ]
        db_guardar_comentarios(test_perfil, comms)
        with basedatos.transaccion() as conn:
            conn.execute("UPDATE social_comentarios SET analizado = 1, tono = 'positivo', analisis_version = ? WHERE id = 'c1'", (ANALISIS_VERSION,))
            conn.execute("UPDATE social_comentarios SET analizado = 1, tono = 'negativo', analisis_version = ? WHERE id = 'c2'", (ANALISIS_VERSION,))

        # 3. Sembrar caché
        post_texto_map = {post1_url: "Post 1 de prueba", post2_url: "Post 2 de prueba"}
        fp = _fingerprint_dataset(comms, post_texto_map)
        res_mock = ResultadoSocial(
            url_perfil=test_perfil,
            posts_analizados=2,
            total_comentarios=2,
            sentimiento={"positivo": 50.0, "neutro": 0.0, "negativo": 50.0, "balance_neto": 0.0},
            distribucion={"positivo": 1, "neutro": 0, "negativo": 1},
            ngramas=[],
            resumen="Resumen de prueba",
            metodo_resumen="cache_test",
        )
        db_guardar_cache(test_perfil, res_mock, max_posts=2, max_comentarios=50, fingerprint=fp)

        # 4. Re-ejecutar consulta con forzar_refresco=False y verificar que NO se llama a Apify
        solicitud = SolicitudSocial(
            url_perfil=test_perfil,
            max_posts=2,
            max_comentarios=50,
            forzar_refresco=False,
        )

        with patch("backend.social.pipeline._obtener_posts") as mock_posts, \
             patch("backend.social.pipeline._obtener_comentarios") as mock_comms, \
             patch("backend.social.db.db_registrar_consumo_apify") as mock_reg:

            resultado = analizar_perfil(solicitud, usuario="test_user")

            # Ni posts ni comentarios deben ser consultados en Apify
            mock_posts.assert_not_called()
            mock_comms.assert_not_called()
            mock_reg.assert_not_called()
            self.assertEqual(resultado.posts_analizados, 2)
            self.assertEqual(resultado.total_comentarios, 2)
            self.assertEqual(resultado.origen_datos, "cache_local")
            self.assertEqual(resultado.comentarios_nuevos, 0)
            self.assertIsNotNone(resultado.sincronizado_en)

    def test_smart_delta_scrape_omite_comentarios_si_bd_al_dia(self):
        """Si Facebook reporta que el post no tiene comentarios nuevos vs BD, omitir scraper de comentarios."""
        from backend.social.scraping import _debe_refrescar_post

        # Caso 1: Facebook tiene 20 comentarios, en BD tenemos 20 comentarios -> NO refrescar
        post_meta = {"comments_count": 20, "fecha": datetime.now(timezone.utc).isoformat()}
        estado_al_dia = {
            "comentarios_scrapeados": 1,
            "max_comentarios_scrapeados": 50,
            "comments_count": 20,
            "total_comentarios_en_bd": 20,
            "comentarios_actualizados_en": datetime.now(timezone.utc).isoformat(),
        }
        self.assertFalse(
            _debe_refrescar_post(
                post_meta=post_meta,
                estado=estado_al_dia,
                max_comentarios=50,
                forzar_refresco=False,
                posicion=0,
            )
        )

        # Caso 2: Facebook reporta 25 comentarios, en BD tenemos 20 comentarios -> SÍ refrescar
        post_meta_nuevos = {"comments_count": 25, "fecha": datetime.now(timezone.utc).isoformat()}
        self.assertTrue(
            _debe_refrescar_post(
                post_meta=post_meta_nuevos,
                estado=estado_al_dia,
                max_comentarios=50,
                forzar_refresco=False,
                posicion=0,
            )
        )

        # Caso 3: forzar_refresco=True siempre fuerza scraping
        self.assertTrue(
            _debe_refrescar_post(
                post_meta=post_meta,
                estado=estado_al_dia,
                max_comentarios=50,
                forzar_refresco=True,
                posicion=0,
            )
        )

    def test_debe_refrescar_perfil_posts_ventana_60_min(self):
        """Verifica la ventana de frescura automática de posts de perfil (60 minutos)."""
        from backend.social.scraping import _debe_refrescar_perfil_posts
        from backend.social.db import db_guardar_posts

        url_perfil = "https://facebook.com/perfil_frescura_test"
        post_url = f"{url_perfil}/posts/1001"

        # Guardar post recién actualizado
        db_guardar_posts(url_perfil, [{
            "url": post_url,
            "texto": "Post reciente de prueba",
            "comments_count": 5,
        }])

        # Dentro de la ventana (<60 min) y sin forzar_refresco -> False (no acudir a Apify)
        debe, posts_db = _debe_refrescar_perfil_posts(url_perfil, max_posts=1, forzar_refresco=False)
        self.assertFalse(debe)
        self.assertEqual(len(posts_db), 1)

        # Con forzar_refresco=True -> True (acudir a Apify)
        debe_forzado, _ = _debe_refrescar_perfil_posts(url_perfil, max_posts=1, forzar_refresco=True)
        self.assertTrue(debe_forzado)

        # Si el usuario pide más posts que los existentes en BD -> True
        debe_mas_posts, _ = _debe_refrescar_perfil_posts(url_perfil, max_posts=5, forzar_refresco=False)
        self.assertTrue(debe_mas_posts)

    def test_api_endpoint_consumo_permisos(self):
        """Verifica el endpoint GET /api/social/consumo con roles y permisos."""
        client = TestClient(app)

        # 1. Petición sin autenticación -> 401
        res_no_auth = client.get("/api/social/consumo")
        self.assertEqual(res_no_auth.status_code, 401)

        # 2. Petición autenticada con rol coordinador
        usuario_coord = {"usuario": "test_coord", "rol": "coordinador", "nombre": "Coordinador Test"}
        app.dependency_overrides[auth.usuario_actual] = lambda: usuario_coord

        db_registrar_consumo_apify("test_coord", "perfil", "actor", "url", 3, 15, "run_coord")

        res_coord = client.get("/api/social/consumo?periodo=todo")
        self.assertEqual(res_coord.status_code, 200)
        data_coord = res_coord.json()
        self.assertEqual(data_coord["usuario"], "test_coord")
        self.assertFalse(data_coord["es_global"])
        self.assertEqual(data_coord["posts_scrapeados"], 3)
        self.assertEqual(data_coord["comentarios_scrapeados"], 15)

        # Si el coordinador intenta pedir vista=global, el backend lo fuerza a su vista personal
        res_coord_global = client.get("/api/social/consumo?periodo=todo&vista=global")
        self.assertEqual(res_coord_global.status_code, 200)
        data_cg = res_coord_global.json()
        self.assertFalse(data_cg["es_global"])
        self.assertEqual(data_cg["usuario"], "test_coord")

        # 3. Petición autenticada con rol admin
        usuario_admin = {"usuario": "test_admin", "rol": "admin", "nombre": "Admin Test"}
        app.dependency_overrides[auth.usuario_actual] = lambda: usuario_admin

        res_admin_global = client.get("/api/social/consumo?periodo=todo&vista=global")
        self.assertEqual(res_admin_global.status_code, 200)
        data_ag = res_admin_global.json()
        self.assertTrue(data_ag["es_global"])
        self.assertGreaterEqual(data_ag["posts_scrapeados"], 3)

    def test_verificar_cuota_scrapeo_limite_posts(self):
        """Bloquea cuando un usuario alcanza o supera el límite de 5,000 posts al mes."""
        usuario = "test_limite_posts"

        # Con 4,999 posts: permitido
        db_registrar_consumo_apify(
            usuario=usuario,
            tipo="perfil",
            actor="apify/facebook-posts-scraper",
            url_objetivo="https://facebook.com/pag",
            posts_scrapeados=4999,
            comentarios_scrapeados=0,
        )
        permitido, msg, info = db_verificar_cuota_scrapeo(usuario)
        self.assertTrue(permitido)
        self.assertEqual(msg, "")
        self.assertFalse(info["excedido"])
        self.assertEqual(info["posts_usados"], 4999)

        # 1 post adicional -> 5,000 posts: bloqueado
        db_registrar_consumo_apify(
            usuario=usuario,
            tipo="perfil",
            actor="apify/facebook-posts-scraper",
            url_objetivo="https://facebook.com/pag",
            posts_scrapeados=1,
            comentarios_scrapeados=0,
        )
        permitido, msg, info = db_verificar_cuota_scrapeo(usuario)
        self.assertFalse(permitido)
        self.assertTrue(info["excedido"])
        self.assertTrue(info["excedido_posts"])
        self.assertIn("5,000", msg)
        self.assertIn("publicaciones", msg)

    def test_verificar_cuota_scrapeo_limite_comentarios(self):
        """Bloquea cuando un usuario alcanza o supera el límite de 75,000 comentarios al mes."""
        usuario = "test_limite_coms"

        # Con 74,999 comentarios: permitido
        db_registrar_consumo_apify(
            usuario=usuario,
            tipo="comentarios",
            actor="apify/facebook-comments-scraper",
            url_objetivo="https://facebook.com/post",
            posts_scrapeados=0,
            comentarios_scrapeados=74999,
        )
        permitido, msg, info = db_verificar_cuota_scrapeo(usuario)
        self.assertTrue(permitido)
        self.assertFalse(info["excedido"])
        self.assertEqual(info["comentarios_usados"], 74999)

        # 1 comentario adicional -> 75,000 comentarios: bloqueado
        db_registrar_consumo_apify(
            usuario=usuario,
            tipo="comentarios",
            actor="apify/facebook-comments-scraper",
            url_objetivo="https://facebook.com/post",
            posts_scrapeados=0,
            comentarios_scrapeados=1,
        )
        permitido, msg, info = db_verificar_cuota_scrapeo(usuario)
        self.assertFalse(permitido)
        self.assertTrue(info["excedido"])
        self.assertTrue(info["excedido_comentarios"])
        self.assertIn("75,000", msg)
        self.assertIn("comentarios", msg)

    def test_scraping_apify_bloqueado_directo_al_superar_cuota(self):
        """_obtener_posts y _obtener_comentarios lanzan CuotaScrapingExcedidaError si el usuario está en el límite."""
        usuario = "test_scrape_blocked"
        db_registrar_consumo_apify(
            usuario=usuario,
            tipo="perfil",
            actor="apify/facebook-posts-scraper",
            url_objetivo="https://facebook.com/pag",
            posts_scrapeados=5000,
            comentarios_scrapeados=0,
        )

        mock_client = MagicMock()

        # Intento de extraer posts
        with self.assertRaises(CuotaScrapingExcedidaError) as ctx_posts:
            _obtener_posts(mock_client, "https://facebook.com/pag", max_posts=5, usuario=usuario)
        self.assertIn("Límite mensual alcanzado", str(ctx_posts.exception))
        # No se debió llamar al actor de Apify
        mock_client.actor.assert_not_called()

        # Intento de extraer comentarios
        with self.assertRaises(CuotaScrapingExcedidaError) as ctx_coms:
            _obtener_comentarios(mock_client, "https://facebook.com/post1", max_por_post=50, usuario=usuario)
        self.assertIn("Límite mensual alcanzado", str(ctx_coms.exception))
        mock_client.actor.assert_not_called()

    def test_endpoint_api_bloquea_con_429_al_exceder_cuota(self):
        """POST /api/social/analizar devuelve HTTP 429 Too Many Requests cuando el usuario superó su cuota."""
        usuario = "test_api_blocked"
        db_registrar_consumo_apify(
            usuario=usuario,
            tipo="perfil",
            actor="apify/facebook-posts-scraper",
            url_objetivo="https://facebook.com/pag",
            posts_scrapeados=5000,
            comentarios_scrapeados=0,
        )

        usuario_dict = {"usuario": usuario, "rol": "coordinador", "nombre": "Coord Blocked"}
        app.dependency_overrides[auth.usuario_actual] = lambda: usuario_dict

        client = TestClient(app)
        res = client.post(
            "/api/social/analizar",
            json={
                "url_perfil": "https://www.facebook.com/PerfilNuevoInexistente",
                "max_posts": 5,
                "max_comentarios": 50,
                "forzar_refresco": True,
            },
        )
        self.assertEqual(res.status_code, 429)
        data = res.json()
        self.assertIn("Límite mensual alcanzado", data["detail"])
        self.assertIn("5,000", data["detail"])

    def test_api_consumo_incluye_campos_cuota_mensual(self):
        """GET /api/social/consumo devuelve límite de 5,000 posts y 75,000 coms, y el status de cuota."""
        usuario = "test_cuota_info"
        db_registrar_consumo_apify(
            usuario=usuario,
            tipo="perfil",
            actor="apify/facebook-posts-scraper",
            url_objetivo="https://facebook.com/pag",
            posts_scrapeados=50,
            comentarios_scrapeados=150,
        )

        usuario_dict = {"usuario": usuario, "rol": "coordinador", "nombre": "Coord Cuota"}
        app.dependency_overrides[auth.usuario_actual] = lambda: usuario_dict

        client = TestClient(app)
        res = client.get("/api/social/consumo?periodo=mes")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["limite_posts_mes"], 5000)
        self.assertEqual(data["limite_comentarios_mes"], 75000)
        self.assertEqual(data["posts_mes"], 50)
        self.assertEqual(data["comentarios_mes"], 150)
        self.assertFalse(data["cuota_excedida"])
        self.assertAlmostEqual(data["porcentaje_posts_mes"], 1.0, places=1)
        self.assertAlmostEqual(data["porcentaje_comentarios_mes"], 0.2, places=1)


if __name__ == "__main__":
    unittest.main()
