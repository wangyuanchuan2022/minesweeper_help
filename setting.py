import json

with open("cfg.json", encoding="utf-8") as f:
    cfg = json.load(f)

num_workers = 8
sleep = 0.02
win_name = cfg["win_name"]  # Minesweeper Arbiter
