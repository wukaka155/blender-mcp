import argparse
import base64
import json
import os
import sys
from pathlib import Path


def _ensure_src_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


_ensure_src_path()
from blender_mcp import server as mcp_server  # noqa: E402


def _print_pass(step: str, detail: str = "") -> None:
    message = f"[PASS] {step}"
    if detail:
        message += f" - {detail}"
    print(message)


def _print_fail(step: str, detail: str) -> None:
    print(f"[FAIL] {step} - {detail}")


def _run_step(step: str, fn):
    try:
        value = fn()
        _print_pass(step)
        return True, value
    except Exception as exc:
        _print_fail(step, str(exc))
        return False, None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live integration smoke test for Blender MCP + Blender addon socket server."
    )
    parser.add_argument("--host", default="localhost", help="Blender addon host (default: localhost)")
    parser.add_argument("--port", type=int, default=9876, help="Blender addon port (default: 9876)")
    parser.add_argument(
        "--skip-screenshot",
        action="store_true",
        help="Skip viewport screenshot test.",
    )
    parser.add_argument(
        "--save-screenshot",
        default="",
        help="Optional output path to save screenshot bytes (for example: ./screenshot.png).",
    )
    parser.add_argument(
        "--skip-generator",
        action="store_true",
        help="Skip generator_model_build test.",
    )
    parser.add_argument(
        "--width",
        type=float,
        default=12.0,
        help="Generator model width (default: 12.0).",
    )
    parser.add_argument(
        "--length",
        type=float,
        default=8.0,
        help="Generator model length (default: 8.0).",
    )
    parser.add_argument(
        "--floor",
        type=int,
        default=6,
        help="Generator model floor count (default: 6).",
    )
    args = parser.parse_args()

    os.environ["BLENDER_HOST"] = args.host
    os.environ["BLENDER_PORT"] = str(args.port)
    mcp_server._blender_connection = None

    print("== Blender MCP Live Integration Check ==")
    print(f"Target: {args.host}:{args.port}")
    print("Note: Blender must be running with addon server connected.")

    checks_passed = 0
    checks_failed = 0

    ok, _ = _run_step("Connect to Blender addon", mcp_server.get_blender_connection)
    if ok:
        checks_passed += 1
    else:
        checks_failed += 1
        print("Stopped because connection failed.")
        return 1

    def _scene_info():
        output = mcp_server.get_scene_info(None)
        parsed = json.loads(output)
        if not isinstance(parsed, dict):
            raise ValueError("Scene info is not a JSON object")
        return parsed

    ok, scene = _run_step("get_scene_info()", _scene_info)
    if ok:
        checks_passed += 1
    else:
        checks_failed += 1
        scene = None

    if scene and scene.get("objects"):
        object_name = scene["objects"][0].get("name")

        def _object_info():
            output = mcp_server.get_object_info(None, object_name)
            parsed = json.loads(output)
            if parsed.get("name") != object_name:
                raise ValueError(f"Object mismatch: expected {object_name}, got {parsed.get('name')}")
            return parsed

        ok, _ = _run_step(f"get_object_info('{object_name}')", _object_info)
        if ok:
            checks_passed += 1
        else:
            checks_failed += 1
    else:
        print("[SKIP] get_object_info() - no objects in scene")

    if not args.skip_screenshot:
        def _extract_image_bytes(image_obj):
            for attr in ("data", "bytes", "blob", "image"):
                payload = getattr(image_obj, attr, None)
                if payload is not None:
                    return payload
            return None

        def _coerce_payload_to_bytes(payload):
            if isinstance(payload, bytes):
                return payload
            if isinstance(payload, bytearray):
                return bytes(payload)
            if isinstance(payload, str):
                try:
                    return base64.b64decode(payload)
                except Exception:
                    return payload.encode("utf-8")
            raise ValueError(f"Unsupported screenshot payload type: {type(payload).__name__}")

        def _screenshot():
            image = mcp_server.get_viewport_screenshot(None, max_size=320)
            payload = _extract_image_bytes(image)
            if payload is None:
                raise ValueError(
                    f"Screenshot returned image object without readable payload fields (type={type(image).__name__})"
                )
            payload_bytes = _coerce_payload_to_bytes(payload)
            if len(payload_bytes) == 0:
                raise ValueError("Screenshot payload is empty")
            return image, payload_bytes

        ok, screenshot_result = _run_step("get_viewport_screenshot()", _screenshot)
        if ok:
            checks_passed += 1
            _, payload = screenshot_result
            payload_size = len(payload)
            _print_pass("Screenshot payload bytes", str(payload_size))
            if args.save_screenshot:
                output_path = Path(args.save_screenshot).expanduser().resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(payload)
                _print_pass("Saved screenshot", str(output_path))
        else:
            checks_failed += 1
    else:
        print("[SKIP] get_viewport_screenshot()")

    if not args.skip_generator:
        def _generator_build():
            result = mcp_server.generator_model_build(None, args.width, args.length, args.floor)
            if isinstance(result, str) and result.strip().startswith("Error"):
                raise ValueError(result)
            return result

        ok, build_result = _run_step(
            f"generator_model_build(width={args.width}, length={args.length}, floor={args.floor})",
            _generator_build,
        )
        if ok:
            checks_passed += 1
            preview = str(build_result)
            if len(preview) > 180:
                preview = preview[:180] + "..."
            _print_pass("Generator result preview", preview)
        else:
            checks_failed += 1
    else:
        print("[SKIP] generator_model_build()")

    print("\n== Summary ==")
    print(f"Passed: {checks_passed}")
    print(f"Failed: {checks_failed}")

    return 0 if checks_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
