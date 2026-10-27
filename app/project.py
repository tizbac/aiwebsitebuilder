"""Project model and scaffolding."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .config import APP_NAME


TEMPLATES = {
    "landing": "Landing page (PHP + CSS/JS)",
    "blank_php": "Blank PHP page",
    "blank_html": "Blank HTML page",
}


class ProjectError(Exception):
    pass


def _slugify(value: str) -> str:
    value = re.sub(r"[^\w\-. ]+", "", value, flags=re.UNICODE).strip()
    value = re.sub(r"\s+", "-", value).strip(".")
    if not value or set(value) <= {"."}:
        return "website"
    return value


class Project:
    """A project whose public document root may be ``web/`` or an existing external directory."""

    STATE_DIRNAME = ".aibuilder"

    def __init__(
        self,
        root: str | Path,
        *,
        create: bool = False,
        web_dir: str | Path | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.state_dir = self.root / self.STATE_DIRNAME
        self.attachments_dir = self.state_dir / "attachments"
        self.screenshots_dir = self.state_dir / "screenshots"
        self.history_path = self.root / "chat_history.json"
        self.meta_path = self.root / "project.json"

        self.meta: dict = {}
        if self.meta_path.exists():
            try:
                self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.meta = {}

        raw_web = web_dir if web_dir is not None else self.meta.get("web_dir")
        if raw_web:
            candidate = Path(str(raw_web)).expanduser()
            if not candidate.is_absolute():
                candidate = self.root / candidate
            self.web_dir = candidate.resolve()
            self.web_dir_is_custom = True
        else:
            self.web_dir = self.root / "web"
            self.web_dir_is_custom = False

        if not create and not self.root.is_dir():
            raise ProjectError(f"Project directory does not exist: {self.root}")
        if self.web_dir_is_custom:
            if not self.web_dir.is_dir():
                raise ProjectError(
                    f"Public web directory does not exist: {self.web_dir}"
                )
        elif not create and not self.web_dir.is_dir():
            raise ProjectError(
                f"{self.root} has no 'web' directory. Choose a project created by "
                f"{APP_NAME} or pick an existing public web directory."
            )
        self._validate_web_dir()
        self.server_url: str = ""
        self.server_port: int = 0

    # ------------------------------------------------------------------ io
    @property
    def name(self) -> str:
        return self.meta.get("name") or self.root.name

    @property
    def web_is_default(self) -> bool:
        return self.web_dir.resolve() == (self.root / "web").resolve()

    def load_meta(self) -> None:
        try:
            self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.meta = {"name": self.root.name}

    def save_meta(self) -> None:
        self.meta.setdefault("name", self.root.name)
        self.meta.setdefault("created", int(time.time()))
        self.meta_path.write_text(
            json.dumps(self.meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def ensure_dirs(self) -> None:
        if not self.web_dir_is_custom:
            self.web_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def _within(self, path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    def _validate_web_dir(self) -> None:
        web = self.web_dir.resolve()
        root = self.root.resolve()
        if web == root:
            raise ProjectError("The public web directory cannot be the project root.")
        if self._within(root, web):
            raise ProjectError(
                "The public web directory cannot be a parent of the project folder."
            )
        if self._within(self.state_dir.resolve(), web):
            raise ProjectError(
                "The public web directory cannot contain the .aibuilder state folder."
            )
        if self.web_dir_is_custom and not self.web_is_default and self._within(web, root):
            raise ProjectError(
                "A custom public web directory must be outside the project folder "
                "(or use the default web/ directory)."
            )

    def rel(self, path: str | Path) -> str:
        """Display path using the logical web/ alias for the public root."""
        candidate = Path(path)
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        web = self.web_dir.resolve()
        root = self.root.resolve()
        if self._within(resolved, web):
            rel = resolved.relative_to(web)
            return "web" if str(rel) == "." else "web/" + rel.as_posix()
        if self._within(resolved, root):
            rel = resolved.relative_to(root)
            return "." if str(rel) == "." else rel.as_posix()
        return str(path)

    def safe_path(self, relative: str) -> Path:
        """Resolve a read path. Reads may access the project and public web root."""
        raw = str(relative or "").strip()
        if not raw:
            raise ProjectError("Path must not be empty.")
        candidate = Path(raw)
        root = self.root.resolve()
        web = self.web_dir.resolve()
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            parts = candidate.parts
            if parts and parts[0] == "web":
                inner = Path(*parts[1:]) if len(parts) > 1 else Path(".")
                resolved = (web / inner).resolve()
            else:
                resolved = (root / candidate).resolve()
        if self._within(resolved, root) or self._within(resolved, web):
            return resolved
        raise ProjectError(
            f"Path '{relative}' is outside the project and its public web directory."
        )

    def web_path(self, relative: str) -> Path:
        """Resolve a write/edit/delete path and require it to be inside web/."""
        raw = str(relative or "").strip()
        if not raw:
            raise ProjectError("Path must not be empty.")
        candidate = Path(raw)
        web = self.web_dir.resolve()
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            parts = candidate.parts
            if parts and parts[0] == "web":
                inner = Path(*parts[1:]) if len(parts) > 1 else Path(".")
            else:
                inner = candidate
            resolved = (web / inner).resolve()
        if not self._within(resolved, web):
            raise ProjectError(
                "Write, edit and delete operations are restricted to the public web/ "
                f"directory. '{relative}' is outside web/."
            )
        return resolved

    # ------------------------------------------------------------- creation
    @classmethod
    def create(
        cls,
        parent_dir: str | Path,
        name: str,
        template: str = "landing",
        web_dir: str | Path | None = None,
    ) -> "Project":
        clean = _slugify(name)
        root = Path(parent_dir).expanduser().resolve() / clean
        if root.exists() and any(root.iterdir()):
            raise ProjectError(f"Directory already exists and is not empty: {root}")
        reuse_existing = web_dir is not None and str(web_dir).strip() != ""
        chosen_web: Path | None = None
        if reuse_existing:
            chosen_web = Path(str(web_dir)).expanduser().resolve()
            if not chosen_web.is_dir():
                raise ProjectError(
                    f"Existing public web directory not found: {chosen_web}"
                )
        root.mkdir(parents=True, exist_ok=True)
        project = cls(root, create=True, web_dir=chosen_web)
        project.ensure_dirs()
        project.meta = {
            "name": name.strip() or clean,
            "created": int(time.time()),
            "template": None if reuse_existing else template,
            "app": APP_NAME,
            "version": 1,
        }
        if reuse_existing:
            project.meta["web_dir"] = str(project.web_dir)
            project.meta["web_reused"] = True
        project.save_meta()
        if not reuse_existing:
            _scaffold(project, template)
        _write_project_extras(project, reuse_existing)
        if not project.history_path.exists():
            project.history_path.write_text(
                json.dumps({"version": 1, "messages": []}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return project


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.lstrip("\n"), encoding="utf-8")


def _scaffold(project: Project, template: str) -> None:
    web = project.web_dir
    if template == "blank_html":
        _write(
            web,
            "index.html",
            """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>New website</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; padding: 3rem; }
  </style>
</head>
<body>
  <h1>Hello!</h1>
  <p>Your new website starts here.</p>
</body>
</html>
""",
        )
    elif template == "blank_php":
        _write(
            web,
            "index.php",
            """<?php $title = 'New website'; ?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title><?= htmlspecialchars($title) ?></title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; padding: 3rem; }
  </style>
</head>
<body>
  <h1><?= htmlspecialchars($title) ?></h1>
  <p>Your new PHP website starts here.</p>
</body>
</html>
""",
        )
    else:
        _write(
            web,
            "index.php",
            """<?php
$year = date('Y');
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="A beautiful new website built with AI Website Builder.">
  <title>Studio &mdash; ideas into interfaces</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="#">Studio<span>.</span></a>
    <nav class="nav">
      <a href="#work">Work</a>
      <a href="#services">Services</a>
      <a href="#contact">Contact</a>
    </nav>
    <a class="button button-ghost" href="#contact">Start a project</a>
  </header>

  <main>
    <section class="hero">
      <p class="eyebrow">Digital product studio</p>
      <h1>We turn rough ideas into<br><em>remarkable</em> interfaces.</h1>
      <p class="lede">Strategy, design and engineering for ambitious teams. Small by
        choice, senior by default, and obsessed with the details that make a product
        feel effortless.</p>
      <div class="actions">
        <a class="button" href="#contact">Book an intro call</a>
        <a class="button button-ghost" href="#work">See selected work</a>
      </div>
      <div class="hero-meta">
        <span>Independent studio</span>
        <span>Working worldwide</span>
        <span>Since 2016</span>
      </div>
    </section>

    <section id="work" class="section">
      <div class="section-head">
        <p class="eyebrow">Selected work</p>
        <h2>Products with purpose.</h2>
      </div>
      <div class="cards">
        <article class="card">
          <div class="swatch swatch-a"></div>
          <h3>Northwind</h3>
          <p>A calmer way to plan complex logistics.</p>
          <span class="tag">Product design</span>
        </article>
        <article class="card">
          <div class="swatch swatch-b"></div>
          <h3>Fieldnotes</h3>
          <p>Research tooling for distributed teams.</p>
          <span class="tag">Web platform</span>
        </article>
        <article class="card">
          <div class="swatch swatch-c"></div>
          <h3>Orbit</h3>
          <p>A publishing home for independent writers.</p>
          <span class="tag">Brand &amp; build</span>
        </article>
      </div>
    </section>

    <section id="services" class="section split">
      <div>
        <p class="eyebrow">What we do</p>
        <h2>From first sketch to shipped product.</h2>
      </div>
      <ul class="services">
        <li><span>01</span><div><h3>Product strategy</h3><p>Clarity on who you serve and why it matters.</p></div></li>
        <li><span>02</span><div><h3>Interface design</h3><p>Systems that feel simple, even when they aren't.</p></div></li>
        <li><span>03</span><div><h3>Engineering</h3><p>Fast, accessible builds that hold up over time.</p></div></li>
      </ul>
    </section>

    <section id="contact" class="cta">
      <h2>Have something in mind?</h2>
      <p>Tell us where you are and where you want to go. We'll reply within two days.</p>
      <a class="button" href="mailto:hello@example.com">hello@example.com</a>
    </section>
  </main>

  <footer class="site-footer">
    <span>&copy; <?= $year ?> Studio.</span>
    <span>Built with care.</span>
  </footer>

  <script src="assets/app.js"></script>
</body>
</html>
""",
        )
        _write(
            web,
            "assets/style.css",
            """:root {
  --ink: #f4f1ea;
  --muted: #a9a49a;
  --bg: #0e0f0e;
  --panel: #171816;
  --line: #2a2b27;
  --accent: #d6ff5f;
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  --sans: "Inter", system-ui, -apple-system, sans-serif;
}

* { box-sizing: border-box; }

html { scroll-behavior: smooth; }

body {
  margin: 0;
  background:
    radial-gradient(1000px 500px at 75% -10%, rgba(214, 255, 95, 0.09), transparent 60%),
    var(--bg);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 16px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

a { color: inherit; text-decoration: none; }

.site-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 2rem;
  padding: 1.5rem clamp(1.25rem, 5vw, 4rem);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  background: rgba(14, 15, 14, 0.82);
  backdrop-filter: blur(14px);
  z-index: 10;
}

.brand { font-size: 1.35rem; font-weight: 800; letter-spacing: -0.04em; }
.brand span { color: var(--accent); }

.nav { display: flex; gap: 1.75rem; color: var(--muted); font-size: 0.9rem; }
.nav a:hover { color: var(--ink); }

.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.8rem 1.3rem;
  border-radius: 999px;
  background: var(--accent);
  color: #10120b;
  font-weight: 600;
  font-size: 0.9rem;
  border: 1px solid var(--accent);
  transition: transform 160ms ease, opacity 160ms ease;
}
.button:hover { transform: translateY(-1px); opacity: 0.92; }
.button-ghost { background: transparent; color: var(--ink); border-color: var(--line); }
.button-ghost:hover { border-color: var(--ink); }

.hero {
  padding: clamp(4rem, 12vw, 9rem) clamp(1.25rem, 5vw, 4rem) clamp(3rem, 8vw, 6rem);
  max-width: 1200px;
  margin: 0 auto;
}

.eyebrow {
  margin: 0 0 1.25rem;
  color: var(--accent);
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

h1, h2, h3 { font-family: var(--serif); font-weight: 500; letter-spacing: -0.025em; }
h1 { font-size: clamp(2.8rem, 7.5vw, 6rem); line-height: 1.02; margin: 0 0 1.75rem; }
h1 em { color: var(--accent); font-style: italic; }
h2 { font-size: clamp(2rem, 4vw, 3.4rem); line-height: 1.08; margin: 0 0 1rem; }
h3 { font-size: 1.3rem; margin: 0 0 0.35rem; }

.lede { max-width: 560px; color: var(--muted); font-size: 1.1rem; margin: 0 0 2rem; }
.actions { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-bottom: 4rem; }

.hero-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.section { padding: clamp(3rem, 7vw, 6rem) clamp(1.25rem, 5vw, 4rem); max-width: 1200px; margin: 0 auto; }
.section-head { margin-bottom: 2.5rem; }

.cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 1rem; }
.card h3 { margin-top: 1.1rem; }
.card p { color: var(--muted); margin: 0.2rem 0 1rem; font-size: 0.92rem; }

.swatch { height: 180px; border-radius: 12px; }
.swatch-a { background: linear-gradient(135deg, #2f3b4c, #7d8fa5 55%, #e1e8f0); }
.swatch-b { background: linear-gradient(135deg, #3c2f45, #a5799a 60%, #ead6e2); }
.swatch-c { background: linear-gradient(135deg, #283b33, #6c9a7d 55%, #d9f3df); }

.tag { font-size: 0.72rem; color: var(--accent); letter-spacing: 0.1em; text-transform: uppercase; }

.split { display: grid; grid-template-columns: 1fr 1fr; gap: 3rem; border-top: 1px solid var(--line); }
.services { list-style: none; margin: 0; padding: 0; }
.services li { display: flex; gap: 1.5rem; padding: 1.5rem 0; border-bottom: 1px solid var(--line); }
.services li > span { color: var(--accent); font-size: 0.8rem; padding-top: 0.35rem; }
.services p { color: var(--muted); margin: 0; font-size: 0.95rem; }

.cta {
  margin: clamp(2rem, 6vw, 5rem) clamp(1.25rem, 5vw, 4rem);
  padding: clamp(2.5rem, 6vw, 5rem);
  border-radius: 24px;
  background: var(--accent);
  color: #10120b;
  text-align: center;
}
.cta h2 { margin-bottom: 0.75rem; }
.cta p { margin: 0 auto 2rem; max-width: 440px; }
.cta .button { background: #10120b; border-color: #10120b; color: var(--accent); }

.site-footer {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 2rem clamp(1.25rem, 5vw, 4rem);
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 0.8rem;
}

@media (max-width: 900px) {
  .site-header { flex-wrap: wrap; }
  .nav { order: 3; width: 100%; gap: 1.25rem; }
  .cards { grid-template-columns: 1fr; }
  .split { grid-template-columns: 1fr; gap: 1.5rem; }
  .swatch { height: 220px; }
}

@media (max-width: 600px) {
  .site-header .button { display: none; }
  .hero-meta { gap: 1rem; font-size: 0.68rem; }
}
""",
        )
        _write(
            web,
            "assets/app.js",
            """// Small progressive-enhancement demo: reveal cards as they scroll into view.
const cards = document.querySelectorAll('.card');
if ('IntersectionObserver' in window && cards.length) {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.animate(
          [{ opacity: 0, transform: 'translateY(16px)' }, { opacity: 1, transform: 'none' }],
          { duration: 500, easing: 'ease-out', fill: 'both' }
        );
        observer.unobserve(entry.target);
      }
    }
  }, { threshold: 0.15 });
  cards.forEach((card) => observer.observe(card));
}
""",
        )

def _write_project_extras(project: Project, reuse_existing: bool) -> None:
    if reuse_existing:
        web_lines = (
            f"- Public web directory (existing): `{project.web_dir}`\n"
            "- Template: not used; existing public content is kept as-is.\n"
            "- Agent path alias: `web/` maps to the public directory above."
        )
    else:
        web_lines = "- Public document root: `web/`"
    readme = f"""# {project.name}

Created with AI Website Builder.

{web_lines}
- Chat history: `chat_history.json`
- Agent state (screenshots, attachments): `.aibuilder/`

The app serves the public web directory with PHP's built-in development server
on a random local port.
"""
    _write(project.root, "README.md", readme)
    _write(
        project.root,
        ".gitignore",
        """.aibuilder/
chat_history.json
""",
    )
