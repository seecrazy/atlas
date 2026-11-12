import json
import pathlib

import pytest

from atlas.config import load_config, write_default_config
from atlas.indexer import index_project


@pytest.fixture()
def project_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "demo"
    root.mkdir()

    retry_dir = root / "retry"
    retry_dir.mkdir()
    (retry_dir / "handler.py").write_text(
        """
from config.retry import load_retry_config

class RetryHandler:
    def execute(self):
        return load_retry_config()

def execute_with_backoff(task):
    return RetryHandler().execute()
""",
        encoding="utf-8",
    )

    client_dir = root / "client"
    client_dir.mkdir()
    (client_dir / "RetryClient.ts").write_text(
        """
import { RetryPolicy } from "./policy";

export class RetryClient {
  send(): boolean {
    return true;
  }
}

export function fetchWithRetry(): void {}
""",
        encoding="utf-8",
    )
    (client_dir / "Gateway.ts").write_text(
        """
import { RetryClient } from "./RetryClient";

export class PaymentGateway {
  pay(): boolean {
    return true;
  }
}
""",
        encoding="utf-8",
    )

    write_default_config(root, overwrite=True)
    config_path = root / ".atlas" / "config.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["project"] = "demo"
    config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    config = load_config(root)
    index_project(config)
    return root

