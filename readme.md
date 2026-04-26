# reedvoid.github.io

Personal blog. Static HTML generated from Markdown, served via GitHub Pages.

## Directory structure

```
content/
  about.md               # section: about
  projects.md            # section: projects
  assets/                # static files — copied to docs/assets/ as-is
  blog/
    YYYYMMDD-title.md    # section: blog   (special — see below)
generator/
  generate.py
  input.css
  requirements.txt
  templates/
    base.html
    blog_index.html
    post.html
    section.html
docs/                    # generated output — GitHub Pages source
```

## Usage

```
pip install -r generator/requirements.txt
python generator/generate.py
```

## Sections

Each `.md` file in `content/` must have a `section:` field in its YAML front matter. The value determines where the output is written:

- **Regular sections** (`section: about`, `section: projects`, etc.) — generate `docs/{section}/index.html`, reachable at `/{section}/`. Add a new section by dropping a new `.md` file in `content/` with the desired `section:` value. The nav bar is hardcoded to blog/about/projects; new sections won't appear there automatically.
- **`section: blog`** — special case. Must live in `content/blog/`. Does not generate a section directory; see Blog below.

Files in `content/` missing a `section:` field are silently skipped.

## Blog

Blog posts require `title:` and `date:` (ISO format: `YYYY-MM-DD`) in addition to `section: blog`.

**URL slug** is derived deterministically as `{date}-{slugified-title}`, e.g. a post titled `Hello World` dated `2026-04-25` becomes `/blog/2026-04-25-hello-world/`.

**Collision detection** — if two posts would produce the same slug, the generator exits with an error listing both source files before writing anything.

**Blog index** is auto-generated, sorted newest-first, and served at both `/` and `/blog/`. There is no `content/` source file for it.

Posts missing `title:` or `date:` are skipped with a warning.

## GitHub Pages setup

Point GitHub Pages to the `docs/` folder on the `main` branch. Nav links use root-relative paths so they work correctly at the root domain.
