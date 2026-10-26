"""Loads ../.env (or ./.env) into the environment, so keys do not have to be exported by hand. Real environment variables win."""
import os, pathlib
if not os.getenv("SK_NO_DOTENV"):
    try:
        from dotenv import load_dotenv
        for p in (pathlib.Path(__file__).parent.parent / ".env", pathlib.Path(__file__).parent / ".env"):
            if p.exists(): load_dotenv(p, override=False)
    except ImportError:
        pass
