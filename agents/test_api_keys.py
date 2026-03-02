"""Quick smoke test: load .env, test each worker profile's LLM endpoint."""

import asyncio
import os
import sys
from pathlib import Path

# Load .env manually (no external dependency)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            if value:  # only set non-empty values
                os.environ.setdefault(key.strip(), value.strip())


async def test_profile(profile_name: str, base_url: str, api_key: str, model_id: str) -> bool:
    """Returns True if we got a non-empty completion, False otherwise."""
    from math_worker.config import LLMConfig
    from math_worker.llm_client import LLMClient

    config = LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model_id=model_id,
        temperature=0.5,
        max_tokens=256,
    )
    client = LLMClient(config)
    try:
        response = await client.complete(
            "You are a helpful assistant. Reply in one short sentence.",
            "Hello, how are you?",
        )
        if response.content:
            print(f"  PASS  Response: {response.content}")
            return True
        print("  FAIL  Got empty completion")
        return False
    except Exception as e:
        print(f"  FAIL  {type(e).__name__}: {e}")
        return False
    finally:
        await client.close()


async def main() -> None:
    from base_agent.worker_config import WorkerProfile

    import yaml

    config_path = Path(__file__).parent / "config.yaml"
    raw = yaml.safe_load(config_path.read_text())
    workers_raw = raw.get("workers", {})

    print(f"Loaded .env from: {env_path}")
    print(f"Loaded workers from: {config_path}\n")

    failures = 0
    tested = 0
    skipped = 0

    for name, profile_data in workers_raw.items():
        try:
            profile = WorkerProfile(**profile_data)
        except (ValueError, Exception) as e:
            err_msg = str(e)
            if "Environment variable" in err_msg and "is not set" in err_msg:
                # Extract just the env var name from the error
                import re

                match = re.search(r"Environment variable '(\w+)' is not set", err_msg)
                var_name = match.group(1) if match else "unknown"
                print(f"[SKIP] {name} — {var_name} not set")
                skipped += 1
            else:
                print(f"[FAIL] {name} — config error: {e}")
                failures += 1
                tested += 1
            print()
            continue

        api_key = profile.llm.api_key.get_secret_value()
        if not api_key:
            print(f"[SKIP] {name} — api_key resolved to empty string")
            skipped += 1
            print()
            continue

        print(f"[TEST] {name} — model={profile.llm.model_id} url={profile.llm.base_url}")
        tested += 1
        passed = await test_profile(name, profile.llm.base_url, api_key, profile.llm.model_id)
        if not passed:
            failures += 1
        print()

    print(f"Results: {tested} tested, {tested - failures} passed, {failures} failed, {skipped} skipped")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
