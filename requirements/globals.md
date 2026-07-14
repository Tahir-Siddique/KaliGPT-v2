[//]: # ( /requirements/global.md )
[//]: # ( SudoHopeX: https://hope.is-a.dev, github.com/SudoHopeX )
[//]: # ( Updated: 21 March 2026 )

# KaliGPT Requirements
This document lists all the requirements/dependencies for KaliGPT, categorized by their source.

## Index
- [GitHub Packages](#github-packages)
- [PyPI (python pip) Packages](#pypi-python-pip-packages)
- [System Packages](#system-packages)
- [Tools](#tools)
- [Love 🩵](#love)


## GitHub Packages
| Package                     | Description                               |
|-----------------------------|-------------------------------------------|
| SudoHopeX/**OpenSearchAPI** | for performing search queries freely.     |
| SudoHopeX/**KaliGPT** 😉    | for using the KaliGPT itself              |


## PyPI (python pip) Packages
| Package                                                                        | Description                          |
|--------------------------------------------------------------------------------|--------------------------------------|
| `openai`                                                                       | accessing openai & OpenRouter models |
| `google-genai`                                                                 | accessing google gemini models       |
| `ollama`                                                                       | using ollama models                  |
| `rich`                                                                         | for rich text output                 |
| `requests`                                                                     | making HTTP requests                 |
| `newspaper3k`,`lxml_html_clean`                                                | cleaning & parsing HTML              |
| `beautifulsoup4`, `flask`, `ddgs`, `curl_cffi`, `nodriver`, `pyvirtualdisplay` | OpenSearchAPI dependency             |
| `prompt_toolkit`                                                               | Interactive CLI Selector             |
| `litellm`                                                                      | LiteLLM gateway provider             |
| `cursor-sdk`                                                                   | Cursor agent / HatsOff Cursor provider |
| `flask`, `pywebview`                                                           | HatsOff desktop UI                   |


## System Packages
| Package                                                                  | Description                                                                   | 
|--------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| `curl`                                                                   | Downloading KaliGPT installer scripts                                         |
| `python3`                                                                | Running Python scripts                                                        |
| `python3-venv`                                                           | Managing & using virtual environments                                         | 
| `python3-pip`                                                            | Installing Python dependencies                                                | 
| `git`                                                                    | Cloning GitHub repositories                                                   |
| `bash`                                                                   | Installing KaliGPT, creating & using KaliGPT launcher                         |
| `python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`, `gir1.2-webkit2-4.1` | HatsOff native window (pywebview GTK); use venv `--system-site-packages`      |
| `lixxml2`, `libxslt`                                                     | for building `lxml_html_clean`                                                |
| `libjpeg-turbo`, `libpng`, `freetype`, `littlecms`,`openjpeg`, `libtiff` | for building `Pillow`                                                         | 
| `make`, `pkg-config`, `clang`                                            | building packages like `Pillow`, `lxml`, `lxml_html_clean`, and `newspaper3k` |
| `rust`                                                                   | for building `newspaper3k` dependencies                                       | 
| `chromium`, `xvfb`                                                       | OpenSearchAPI dependency                                                      |

## Tools
| Name                                                         | Description                                                               |
|--------------------------------------------------------------|---------------------------------------------------------------------------|
| `keyword_search`, `check_search_connection`, `search_as_RAG` | for online search & real-time info. connectivity                          |
| `get_local_server_content`                                   | extracting content accessible via local server (e.g. localhost etc.)      |
| `execute_generic_linux_command`                              | executing linux tools & commands                                          |


## Love
Lots of ❤️, Care and ⭐ from the community!
