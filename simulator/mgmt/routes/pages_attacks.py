# simulator/mgmt/routes/pages_attacks.py
import os
import re
import yaml
from flask import render_template, abort, redirect
from . import bp

def load_yaml_files(directory):
    attacks = []
    for filename in os.listdir(directory):
        if filename.endswith(".yaml"):
            with open(os.path.join(directory, filename), "r") as f:
                y = yaml.safe_load(f) or {}
                order = y.get("order", float("inf"))
                attacks.append({
                    "order": order,
                    "title": y.get("title", "No Title"),
                    "link": f"/attacks/{os.path.basename(directory)}/{filename.replace('.yaml', '')}",
                })
    attacks.sort(key=lambda x: x["order"])
    return attacks

def slugify(title: str) -> str:
    s = title.strip()
    s = re.sub(r"[ _]+", "-", s)
    s = re.sub(r"[^A-Za-z0-9\-\&]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s

SLUG_OVERRIDES = {}

@bp.route("/attacks/all")
@bp.route("/attacks")
def attacks_index():
    base_dir = "templates/pages/attacks"
    categories = {
        "Reconnaissance": load_yaml_files(os.path.join(base_dir, "recon")),
        "Protocol Tampering": load_yaml_files(os.path.join(base_dir, "tampering")),
        "Denial of Service": load_yaml_files(os.path.join(base_dir, "dos")),
        "Injection": load_yaml_files(os.path.join(base_dir, "injection")),
        "Exfiltration": load_yaml_files(os.path.join(base_dir, "exfiltration")),
        "Firmware Attacks": load_yaml_files(os.path.join(base_dir, "firmware")),
    }
    return render_template("pages/attacks/list.html", section="attacks", sub_section="", current_page="attacks", categories=categories)

@bp.route("/attacks/<tactic>/<filename>")
def redirect_attack_scenario(tactic: str, filename: str):
    base_name = filename.rsplit(".", 1)[0]
    yaml_path = os.path.join("templates", "pages", "attacks", tactic, f"{base_name}.yaml")
    if not os.path.exists(yaml_path):
        abort(404)

    with open(yaml_path, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f) or {}

    title = y.get("title", base_name)
    wiki_slug = SLUG_OVERRIDES.get(title, slugify(title))
    wiki_url = f"https://github.com/nicholasaleks/Damn-Vulnerable-Drone/wiki/{wiki_slug}"
    return redirect(wiki_url, code=302)
