# Sourced by local scripts that call gcloud. IAP tunnels are slow unless gcloud's Python
# has NumPy, so use a dedicated venv when one exists, never this repo's .venv:
#   uv venv --python 3.12 ~/.local/share/gcloud-python
#   VIRTUAL_ENV=~/.local/share/gcloud-python uv pip install numpy
if [[ -z "${CLOUDSDK_PYTHON:-}" && -x "$HOME/.local/share/gcloud-python/bin/python" ]]; then
  export CLOUDSDK_PYTHON="$HOME/.local/share/gcloud-python/bin/python"
fi
