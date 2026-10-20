"""Start an isolated synthetic fixture, with realistic-sized immutable text."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
root = args.output.resolve()
if not root.is_relative_to(Path("D:/Artifacts/GraspMemoEdu").resolve()) or root.exists() or args.port in (8765, 18768):
    raise ValueError("Use a new isolated artifact directory and port")
sys.path[:0] = [str(args.source), str(args.source / "tests")]
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from tests.fixtures import DeterministicJudge
from tests.subject_matrix_fixture import write_matrix

with socket.socket() as probe:
    probe.bind(("127.0.0.1", args.port))
topics = write_matrix(root / "data")
for topic in topics.values():
    if topic["id"].startswith("lesson-"):
        body = "".join("<p>Performance fixture " + " ".join(
            hashlib.sha256(str(i).encode()).hexdigest()[j:j+8] for j in range(0, 64, 8))
            + "</p>" for i in range(1500))
        topic["introduction"]["html"] += body
        topic["modules"][0]["example"]["html"] += body
        (root / "data" / (topic["id"] + ".json")).write_text(json.dumps(topic), encoding="utf-8")
app = create_app(Settings(data_dir=root / "data", port=args.port),
    LearningService(topics["lesson-math"], root / "data", DeterministicJudge()))
account = app.state.access_store.create_account("Performance fixture", is_student=True, purpose="test")
(root / "account.private.json").write_text(json.dumps(account), encoding="utf-8")
print("READY isolated performance fixture", flush=True)
uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
