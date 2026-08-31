# 3D Scene Viewer

## Install

Run these commands from the repository root:

```bash
npm ci
python3 -m pip install -e '.[test]'
```


## Launch using

```bash
python3 -m scene_agent
```

This starts the local HTTP server and serves the workbench. Open
<http://127.0.0.1:8765> in a browser on the same machine. Keep the terminal
running while you use the UI.

## Run tests

```bash
python3 -m pytest
```
