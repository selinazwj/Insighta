# Debug Report for Insighta AI Growth Package

## What was checked

- Python syntax compilation for all extension modules and scripts:
  - `app/ai_growth/*.py`
  - `scripts/*.py`
- Static inspection of jump, prediction, routing, security, and installer logic.
- Installer smoke tests using a synthetic Insighta-like repository in two layouts:
  1. Nested package layout: `repo/pkg/scripts/install_ai_growth.py`
  2. Direct unpack layout: `repo/scripts/install_ai_growth.py` and `repo/app/ai_growth/*`
- GitHub repository structure was rechecked online because the target repository is public and may change.

## Fixed issues

1. **Packaging / install path bug**

   The previous zip contained a top-level folder and the README implied running `python scripts/install_ai_growth.py` directly after extraction. That could be confusing. More importantly, if unpacked directly into the repo, the old installer could delete `app/ai_growth` while trying to copy it onto itself.

   Fixed by:
   - making the installer auto-detect the Insighta repository root;
   - supporting both nested and direct-unpack layouts;
   - skipping copy safely when source and destination are already the same path;
   - adding optional `--repo-root /path/to/Insighta` support.

2. **External return HTML escaping**

   The external return page rendered survey title and token directly in HTML. This was low-probability but unsafe if a survey title contained HTML.

   Fixed by escaping title and token before rendering the confirmation page.

3. **FastAPI route signature cleanup**

   The completion route used `request: Request = None`. It worked syntactically, but it was not ideal for FastAPI request injection.

   Fixed by using `request: Request` as an explicit injected parameter.

4. **Invalid JSON body robustness**

   `recompute` and `impression` APIs could throw a raw 500 if `survey_id` was malformed.

   Fixed by returning a clean 400 for recompute and safely ignoring invalid impression survey IDs.

## Remaining notes

- Full runtime testing against the live repository database could not be executed in this sandbox because SQLAlchemy is not installed here and the remote repository could not be cloned from the container. The code itself was syntax-compiled and the patch installer was smoke-tested against representative files.
- In the actual Insighta environment, run:

```bash
python scripts/install_ai_growth.py
python -m py_compile api/main.py app/ai_growth/*.py scripts/*.py
python scripts/create_ai_growth_tables.py
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

- If the installer cannot locate the repository root, run:

```bash
python /path/to/extracted/scripts/install_ai_growth.py --repo-root /path/to/Insighta
```
