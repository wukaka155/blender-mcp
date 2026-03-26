import json
import os
import socket
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _install_fake_mcp():
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def tool(self):
            def decorator(func):
                return func

            return decorator

        def prompt(self):
            def decorator(func):
                return func

            return decorator

        def run(self):
            return None

    @dataclass
    class _FakeImage:
        data: bytes
        format: str

    class _FakeContext:
        pass

    fastmcp_module.FastMCP = _FakeFastMCP
    fastmcp_module.Context = _FakeContext
    fastmcp_module.Image = _FakeImage

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module.server = server_module

    sys.modules.setdefault("mcp", mcp_module)
    sys.modules.setdefault("mcp.server", server_module)
    sys.modules.setdefault("mcp.server.fastmcp", fastmcp_module)


_install_fake_mcp()

from blender_mcp import server


class DummySocket:
    def __init__(self):
        self.sent = []
        self.timeout = None

    def sendall(self, payload):
        self.sent.append(payload)

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        return None


class DummyBlender:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def send_command(self, command, params=None):
        self.calls.append((command, params))
        return self.result


class ServerTests(unittest.TestCase):
    def setUp(self):
        server._blender_connection = None

    def tearDown(self):
        server._blender_connection = None

    def test_get_blender_connection_creates_and_caches(self):
        created = []

        class FakeConnection:
            def __init__(self, host, port):
                self.host = host
                self.port = port
                created.append(self)

            def connect(self):
                return True

        with mock.patch.object(server, "BlenderConnection", FakeConnection):
            with mock.patch.dict("os.environ", {"BLENDER_HOST": "127.0.0.1", "BLENDER_PORT": "1234"}):
                first = server.get_blender_connection()
                second = server.get_blender_connection()

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].host, "127.0.0.1")
        self.assertEqual(created[0].port, 1234)

    def test_get_blender_connection_raises_when_connect_fails(self):
        class FakeConnection:
            def __init__(self, host, port):
                self.host = host
                self.port = port

            def connect(self):
                return False

        with mock.patch.object(server, "BlenderConnection", FakeConnection):
            with self.assertRaisesRegex(Exception, "Could not connect to Blender"):
                server.get_blender_connection()

        self.assertIsNone(server._blender_connection)

    def test_send_command_success(self):
        conn = server.BlenderConnection(host="localhost", port=9876)
        conn.sock = DummySocket()
        response = {"status": "success", "result": {"ok": True}}

        with mock.patch.object(conn, "receive_full_response", return_value=json.dumps(response).encode("utf-8")):
            result = conn.send_command("ping", {"a": 1})

        self.assertEqual(result, {"ok": True})
        sent_payload = json.loads(conn.sock.sent[0].decode("utf-8"))
        self.assertEqual(sent_payload, {"type": "ping", "params": {"a": 1}})

    def test_send_command_error_status_is_wrapped(self):
        conn = server.BlenderConnection(host="localhost", port=9876)
        conn.sock = DummySocket()
        response = {"status": "error", "message": "bad"}

        with mock.patch.object(conn, "receive_full_response", return_value=json.dumps(response).encode("utf-8")):
            with self.assertRaisesRegex(Exception, "Communication error with Blender: bad"):
                conn.send_command("ping")

        self.assertIsNone(conn.sock)

    def test_send_command_timeout_resets_socket(self):
        conn = server.BlenderConnection(host="localhost", port=9876)
        conn.sock = DummySocket()

        with mock.patch.object(conn, "receive_full_response", side_effect=socket.timeout("timed out")):
            with self.assertRaisesRegex(Exception, "Timeout waiting for Blender response"):
                conn.send_command("slow")

        self.assertIsNone(conn.sock)

    def test_get_scene_info_returns_json(self):
        dummy = DummyBlender({"name": "Scene", "object_count": 2})
        with mock.patch.object(server, "get_blender_connection", return_value=dummy):
            out = server.get_scene_info(None)

        parsed = json.loads(out)
        self.assertEqual(parsed["name"], "Scene")
        self.assertEqual(dummy.calls, [("get_scene_info", None)])

    def test_get_object_info_returns_json(self):
        dummy = DummyBlender({"name": "Cube", "type": "MESH"})
        with mock.patch.object(server, "get_blender_connection", return_value=dummy):
            out = server.get_object_info(None, "Cube")

        parsed = json.loads(out)
        self.assertEqual(parsed["name"], "Cube")
        self.assertEqual(dummy.calls, [("get_object_info", {"name": "Cube"})])

    def test_generator_model_build_passes_params(self):
        dummy = DummyBlender({"status": "started", "id": "g1"})
        with mock.patch.object(server, "get_blender_connection", return_value=dummy):
            out = server.generator_model_build(None, 12.5, 8.0, 6)

        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "started")
        self.assertEqual(
            dummy.calls,
            [
                (
                    "generator_model_build",
                    {"width": 12.5, "length": 8.0, "floor": 6, "model_name": "BaseBuild"},
                )
            ],
        )

    def test_export_model_glb_passes_export_path(self):
        dummy = DummyBlender({"success": True, "filepath": "C:/tmp/out.glb"})
        with mock.patch.object(server, "get_blender_connection", return_value=dummy):
            out = server.export_model_glb(None, "Tower", "C:/tmp/out.glb")

        parsed = json.loads(out)
        self.assertTrue(parsed["success"])
        self.assertEqual(
            dummy.calls,
            [("export_model_glb", {"model_name": "Tower", "export_path": "C:/tmp/out.glb"})],
        )

    def test_open_project_file_passes_path(self):
        dummy = DummyBlender({"success": True, "filepath": "C:/tmp/scene.blend"})
        with mock.patch.object(server, "get_blender_connection", return_value=dummy):
            out = server.open_project_file(None, "C:/tmp/scene.blend")

        parsed = json.loads(out)
        self.assertTrue(parsed["success"])
        self.assertEqual(
            dummy.calls,
            [("open_project_file", {"file_path": "C:/tmp/scene.blend"})],
        )

    def test_get_viewport_screenshot_blackbox_success(self):
        class ScreenshotBlender:
            def __init__(self):
                self.calls = []

            def send_command(self, command, params=None):
                self.calls.append((command, params))
                with open(params["filepath"], "wb") as handle:
                    handle.write(b"\x89PNG\r\n\x1a\nfake")
                return {"success": True}

        dummy = ScreenshotBlender()
        with mock.patch.object(server, "get_blender_connection", return_value=dummy):
            image = server.get_viewport_screenshot(None, max_size=256)

        self.assertEqual(image.format, "png")
        self.assertTrue(image.data.startswith(b"\x89PNG"))
        self.assertEqual(dummy.calls[0][0], "get_viewport_screenshot")
        self.assertEqual(dummy.calls[0][1]["max_size"], 256)
        self.assertEqual(dummy.calls[0][1]["format"], "png")
        self.assertFalse(os.path.exists(dummy.calls[0][1]["filepath"]))

    def test_asset_creation_strategy_no_removed_integrations(self):
        prompt = server.asset_creation_strategy()
        self.assertNotIn("hunyuan", prompt.lower())
        self.assertNotIn("polyhaven", prompt.lower())
        self.assertNotIn("sketchfab", prompt.lower())
        self.assertNotIn("hyper3d", prompt.lower())


if __name__ == "__main__":
    unittest.main()
