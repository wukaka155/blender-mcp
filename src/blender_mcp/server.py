import asyncio
import json
import logging
from contextlib import asynccontextmanager
import os
import socket
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from mcp.server.fastmcp import Context, FastMCP, Image

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876
DEFAULT_MCP_SSE_HOST = os.getenv("MCP_SSE_HOST", "0.0.0.0")
DEFAULT_MCP_SSE_PORT = int(os.getenv("MCP_SSE_PORT", "1134"))
SSE_ENDPOINT = "/sse"
MESSAGES_ENDPOINT = "/messages/"
BLENDER_SOCKET_TIMEOUT_SECONDS = 180.0
BLENDER_SOCKET_POLL_SECONDS = 1.0
SCREENSHOT_FORMAT = "png"


def configure_windows_event_loop_policy() -> None:
    """Avoid noisy Proactor socket shutdown errors when peers disconnect on Windows."""
    if not sys.platform.startswith("win"):
        return

    selector_policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy_cls is None:
        return

    current_policy = asyncio.get_event_loop_policy()
    if isinstance(current_policy, selector_policy_cls):
        return

    asyncio.set_event_loop_policy(selector_policy_cls())
    logger.info("Using WindowsSelectorEventLoopPolicy for stable socket shutdown behavior")

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket | None = None

    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Blender addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock: socket.socket, buffer_size: int = 8192) -> bytes:
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        deadline = time.monotonic() + BLENDER_SOCKET_TIMEOUT_SECONDS
        sock.settimeout(BLENDER_SOCKET_POLL_SECONDS)

        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break

                    chunks.append(chunk)

                    # Check if we've received a complete JSON object
                    try:
                        data = b"".join(chunks)
                        json.loads(data.decode("utf-8"))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    if time.monotonic() >= deadline:
                        logger.warning("Socket timeout during chunked receive")
                        break
                    continue
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        if chunks:
            data = b"".join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                json.loads(data.decode("utf-8"))
                return data
            except json.JSONDecodeError:
                raise Exception("Incomplete JSON response received")

        raise Exception("No data received")

    def _invalidate_socket(self) -> None:
        self.sock = None

    def send_command(self, command_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a command to Blender and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")

        command = {
            "type": command_type,
            "params": params or {},
        }

        try:
            logger.info(f"Sending command: {command_type} with params: {params}")
            self.sock.sendall(json.dumps(command).encode("utf-8"))
            logger.info(f"Command sent, waiting for response...")

            self.sock.settimeout(BLENDER_SOCKET_POLL_SECONDS)
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")

            response = json.loads(response_data.decode("utf-8"))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")

            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))

            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            self._invalidate_socket()
            raise Exception("Timeout waiting for Blender response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self._invalidate_socket()
            raise Exception(f"Connection to Blender lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            if "response_data" in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            self._invalidate_socket()
            raise Exception(f"Communication error with Blender: {str(e)}")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools

    try:
        # Just log that we're starting up
        logger.info("BlenderMCP server starting up")

        # Try to connect to Blender on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")

        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _blender_connection
        if _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCP server shut down")

# Create the MCP server with lifespan support
mcp = FastMCP(
    "BlenderMCP",
    lifespan=server_lifespan,
    host=DEFAULT_MCP_SSE_HOST,
    port=DEFAULT_MCP_SSE_PORT,
)



_blender_connection = None


def get_blender_connection() -> BlenderConnection:
    """Get or create a persistent Blender connection"""
    global _blender_connection

    # Create a new connection if needed
    if _blender_connection is None:
        host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
        port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
        _blender_connection = BlenderConnection(host=host, port=port)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")
    return _blender_connection


def _format_tool_result(result: Any) -> str:
    if isinstance(result, (dict, list)):
        return json.dumps(result, indent=2, ensure_ascii=False)
    return str(result)


def _run_blender_tool(command: str, params: dict[str, Any] | None = None) -> Any:
    blender = get_blender_connection()
    return blender.send_command(command, params)


def _run_blender_tool_as_text(command: str, params: dict[str, Any] | None = None) -> str:
    return _format_tool_result(_run_blender_tool(command, params))


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene"""
    try:
        return _run_blender_tool_as_text("get_scene_info")
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return f"Error getting scene info: {str(e)}"

@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """
    Get detailed information about a specific object in the Blender scene.
    
    Parameters:
    - object_name: The name of the object to get information about
    """
    try:
        return _run_blender_tool_as_text("get_object_info", {"name": object_name})
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return f"Error getting object info: {str(e)}"


def _screenshot_temp_path() -> str:
    temp_dir = tempfile.gettempdir()
    return os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")


@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """
    Capture a screenshot of the current Blender 3D viewport.
    
    Parameters:
    - max_size: Maximum size in pixels for the largest dimension (default: 800)
    
    Returns the screenshot as an Image.
    """
    temp_path = _screenshot_temp_path()
    try:
        result = _run_blender_tool(
            "get_viewport_screenshot",
            {"max_size": max_size, "filepath": temp_path, "format": SCREENSHOT_FORMAT},
        )

        if "error" in result:
            raise Exception(result["error"])

        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")

        with open(temp_path, "rb") as f:
            image_bytes = f.read()

        return Image(data=image_bytes, format=SCREENSHOT_FORMAT)

    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}")
        raise Exception(f"Screenshot failed: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@mcp.tool()
def generator_model_build(
    ctx: Context,
    width: float,
    length: float,
    floor: int,
    model_name: str = "BaseBuild",
) -> str:
    """
    Build a generator model through Blender addon logic.

    Parameters:
    - width: Building width
    - length: Building length
    - floor: Number of floors
    - model_name: Name of generated model object (default: BaseBuild)
    """
    try:
        return _run_blender_tool_as_text(
            "generator_model_build",
            {
                "width": width,
                "length": length,
                "floor": floor,
                "model_name": model_name,
            },
        )
    except Exception as e:
        logger.error(f"Error running generator_model_build: {str(e)}")
        return f"Error running generator_model_build: {str(e)}"

@mcp.tool()
def export_model_glb(ctx: Context, model_name: str, export_path: str = "") -> str:
    """
    Export scene/model to a GLB file through Blender addon logic.

    Parameters:
    - model_name: Name of the Blender object/model to export
    - export_path: Optional output .glb path. Uses default export directory when empty.
    """
    try:
        return _run_blender_tool_as_text(
            "export_model_glb",
            {"model_name": model_name, "export_path": export_path},
        )
    except Exception as e:
        logger.error(f"Error running export_model_glb: {str(e)}")
        return f"Error running export_model_glb: {str(e)}"

@mcp.tool()
def open_project_file(ctx: Context, file_path: str) -> str:
    """
    Open a Blender project (.blend) file from a specific path.

    Parameters:
    - file_path: Absolute or relative path to the target .blend file
    """
    try:
        return _run_blender_tool_as_text("open_project_file", {"file_path": file_path})
    except Exception as e:
        logger.error(f"Error running open_project_file: {str(e)}")
        return f"Error running open_project_file: {str(e)}"

@mcp.prompt()
def asset_creation_strategy() -> str:
    """Defines the preferred strategy for creating assets in Blender"""
    return """When creating 3D content in Blender, always start by checking if integrations are available:

    0. Before anything, always check the scene from get_scene_info()
    1. Always check the world_bounding_box for each item so that:
        - Ensure that all objects that should not be clipping are not clipping.
        - Items have right spatial relationship.

    Only fall back to scripting when:
    - A simple primitive is explicitly requested
    - The task specifically requires a basic material/color
    """

# Main execution
async def run_sse_server() -> None:
    """Run the MCP server over SSE transport."""
    from mcp.server.sse import SseServerTransport
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    sse = SseServerTransport(MESSAGES_ENDPOINT)

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0],
                streams[1],
                mcp._mcp_server.create_initialization_options(),
            )

    async def root_metadata(request):
        base_url = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
            "name": "BlenderMCP",
            "transport": "sse",
            "sse_endpoint": f"{base_url}{SSE_ENDPOINT}",
            "messages_endpoint": f"{base_url}{MESSAGES_ENDPOINT}",
            }
        )

    async def health(_request):
        return JSONResponse({"status": "ok"})

    routes = [
        Route("/", endpoint=root_metadata),
        Route("/health", endpoint=health),
        Route(SSE_ENDPOINT, endpoint=handle_sse),
        Mount(MESSAGES_ENDPOINT, app=sse.handle_post_message),
    ]

    app = Starlette(
        debug=mcp.settings.debug,
        routes=routes,
    )

    config = uvicorn.Config(
        app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    logger.info(
        "Starting BlenderMCP SSE server on http://%s:%s%s",
        mcp.settings.host,
        mcp.settings.port,
        SSE_ENDPOINT,
    )
    await server.serve()

def main():
    """Run the MCP server in SSE mode."""
    import anyio
    configure_windows_event_loop_policy()
    try:
        anyio.run(run_sse_server)
    except KeyboardInterrupt:
        logger.info("Received Ctrl+C, shutting down BlenderMCP server")

if __name__ == "__main__":
    main()
