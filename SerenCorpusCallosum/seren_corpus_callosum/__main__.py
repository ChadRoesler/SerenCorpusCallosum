"""
Entry point: python -m seren_corpus_callosum [--config path]
             (also the `seren-corpus-callosum` console script)

Boots the FastAPI app with uvicorn using the resolved config. The callosum
owns no store of its own — it just needs to be up and reachable so a model (or
another service) can POST /search and get the merged, ranked recall back.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from .app import create_app
from .config import load_config


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 regardless of OS locale. The same Windows-
    codepage backstop the rest of the family carries: PYTHONUTF8=1 in the
    service env is the primary fix; this covers the hand-run `python -m ...`
    case. No-op where stdio is already UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _maybe_inject_truststore(cfg, log=print) -> None:
    """If tls.trust_system_store is on, route Python TLS through the OS trust
    store via `truststore`. For the callosum this matters at FAN time: every
    /search makes outbound httpx calls to the configured stores, and if any of
    those is an https endpoint behind a corp proxy, the handshake fails with
    CERTIFICATE_VERIFY_FAILED unless the OS root CA is honored. Inject before
    create_app so the transport is built into an already-trusting process.
    Gated + logged, never silent."""
    if not cfg.tls.trust_system_store:
        return
    try:
        import truststore
    except ImportError:
        log("[seren-corpus-callosum] tls.trust_system_store is ON but "
            "'truststore' isn't installed. Install the corp extra: "
            "pip install 'seren-corpus-callosum[corp]' (continuing with certifi defaults).")
        return
    truststore.inject_into_ssl()
    log("[seren-corpus-callosum] TLS: using OS trust store (truststore injected)")


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="seren_corpus_callosum",
        description="SerenCorpusCallosum - read-only N-store memory federation. The callosum.")
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to seren-corpus-callosum.yaml (default: ./seren-corpus-callosum.yaml "
             "or $SEREN_SCC_CONFIG, falling back to built-in defaults).")
    args = parser.parse_args()

    cfg = load_config(args.config)
    _maybe_inject_truststore(cfg)
    app = create_app(cfg)

    print(f"[seren-corpus-callosum] listening on {cfg.server.host}:{cfg.server.port}")
    print(f"[seren-corpus-callosum] fanning {len(cfg.federation.stores)} configured store(s)")
    print(f"[seren-corpus-callosum] auth: "
          f"{'enabled' if cfg.server.bearer_token else 'DISABLED (no token)'}")

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")


if __name__ == "__main__":
    main()
