# 🧰 healthchecks-io-bulk-editor

A command-line tool to **bulk edit [Healthchecks.io](https://healthchecks.io)** checks — rename, retag, pause, or update schedules in batches.

Built with [click](https://click.palletsprojects.com/), [loguru](https://github.com/Delgan/loguru`), and the [healthchecks-io](https://github.com/andrewthetechie/py-healthchecks.io) Python client (forked & improved).

---

## ✨ Features

- Filter checks by **tag**, **name regex**, **slug regex**, or **status**
- Bulk-update attributes:
  - name, description, tags, timeout, grace, schedule, timezone, methods, channels
- Add/remove/replace tags
- Pause or resume checks
- Optional **dry-run mode** for safety
- Progress bar + log output with `tqdm` and `loguru`
- Works with self-hosted Healthchecks instances

---

## 🚀 Installation

### Using Poetry (recommended for development)
```bash
git clone https://gitea.wavyzz.com/Wavyzz/healthchecks-io-bulk-editor.git
cd healthchecks-io-bulk-editor
poetry install
````

---

## 💡 Usage

```bash
hc-bulk --help
Usage: hc-bulk [OPTIONS] COMMAND [ARGS]...

  Bulk tools for Healthchecks.io.

Options:
  --version   Show the version and exit.
  -h, --help  Show this message and exit.

Commands:
  bulk-update  Bulk edit checks: select by filters, then apply updates...
  ls           List checks after applying filters.
```

### Examples

```bash
hc-bulk ls --tags backup,docker
hc-bulk bulk-update --name-re "docker-system" --set-grace 3600 --set-schedule "30 3 * * 0"
```

## 🐳 Docker

A Docker image is available on Docker Hub: `estebanthi/hc-bulk`.

```bash
docker run --rm \
    -e HC_API_KEY="your_api_key_here" \
    -e HC_API_URL="https://hc.example.com/api" \
    estebanthi/hc-bulk:latest  \
    ls --tags your_tag_here
```
