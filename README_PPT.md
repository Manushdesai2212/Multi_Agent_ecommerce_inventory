Presentation build instructions

This repository includes a generator to build a PowerPoint from a Markdown outline.

Prerequisites
- Python 3.8+
- pip

Install dependencies

```bash
pip install -r requirements.txt
```

Build the PPTX

```bash
python scripts/build_presentation.py
```

The script reads `presentation_outline.md` and writes `multi_agent_presentation.pptx` in the repository root.

If you want me to generate the PPTX here, allow me to install `python-pptx` and run the script (I will request network permission). If you prefer to run locally, follow the steps above.
