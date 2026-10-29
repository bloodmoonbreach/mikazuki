"""Run with `mikazuki` or `python -m mikazuki`. Host/port from MIKAZUKI_HOST / MIKAZUKI_PORT."""

import os


def main() -> None:
    import uvicorn

    uvicorn.run("mikazuki.app:app", host=os.environ.get("MIKAZUKI_HOST", "127.0.0.1"),
                port=int(os.environ.get("MIKAZUKI_PORT", "8780")), proxy_headers=False)


if __name__ == "__main__":
    main()
