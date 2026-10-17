"""The v3 env server: airops-v3-day on streamable HTTP, beside v1's server.py."""

import os

from .live import AirOpsV3LiveEnv


def main() -> None:
    AirOpsV3LiveEnv().create_app().run(transport="http", host=os.environ.get("MCP_HOST", "0.0.0.0"),
                                       port=int(os.environ.get("MCP_PORT", "18765")), show_banner=False)


if __name__ == "__main__":
    main()
