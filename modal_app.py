import modal

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements("requirements.txt")
    .add_local_dir("static", "/root/static")
    .add_local_python_source("server", "agents", "models", "prompts", "voice")
)
app = modal.App("tars")


@app.function(image=image, secrets=[modal.Secret.from_dotenv()],
              min_containers=1, max_containers=1, timeout=3600)
@modal.concurrent(max_inputs=50)
@modal.asgi_app(label="tars")
def web():
    """One always-on container serving the page and every phone's live connection."""
    from server import app as site
    return site
