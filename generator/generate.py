import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import yaml
import markdown
from jinja2 import Environment, FileSystemLoader

REPO_ROOT = Path(__file__).parent.parent
GENERATOR_DIR = Path(__file__).parent
PREVIEW_WORD_LIMIT = 45
CONTENT_DIR = REPO_ROOT / "content"
ASSETS_DIR = CONTENT_DIR / "assets"
OUTPUT_DIR = REPO_ROOT / "docs"
TEMPLATES_DIR = GENERATOR_DIR / "templates"


def slugify(text):
    text = str(text).lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def post_slug(title, post_date):
    if isinstance(post_date, (date, datetime)):
        date_str = post_date.strftime('%Y-%m-%d')
    else:
        date_str = str(post_date)
    return f"{date_str}-{slugify(title)}"


def format_date(post_date):
    if isinstance(post_date, (date, datetime)):
        return post_date.strftime('%B %-d, %Y')
    return str(post_date)


def parse_md_file(filepath):
    with open(filepath, encoding='utf-8') as f:
        raw = f.read()

    if not raw.startswith('---'):
        return {}, raw

    parts = raw.split('---', 2)
    if len(parts) < 3:
        return {}, raw

    front_matter = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return front_matter, body


def render_markdown(body):
    md = markdown.Markdown(extensions=['fenced_code', 'tables'])
    html = md.convert(body)
    html = re.sub(
        r'<a href="(https?://[^"]*)"',
        r'<a href="\1" target="_blank" rel="noopener noreferrer"',
        html
    )
    return html


def make_preview(body, word_limit=PREVIEW_WORD_LIMIT):
    words = body.split()
    if len(words) <= word_limit:
        return body
    return ' '.join(words[:word_limit]) + '...'


def collect_posts():
    posts = []
    blog_dir = CONTENT_DIR / "blog"

    if not blog_dir.exists():
        return posts

    for md_file in sorted(blog_dir.glob("*.md")):
        front_matter, body = parse_md_file(md_file)

        title = front_matter.get('title')
        post_date = front_matter.get('date')

        if not title:
            print(f"WARNING: {md_file} missing 'title', skipping.")
            continue
        if not post_date:
            print(f"WARNING: {md_file} missing 'date', skipping.")
            continue

        slug = post_slug(title, post_date)
        posts.append({
            'slug': slug,
            'title': title,
            'date': post_date,
            'date_display': format_date(post_date),
            'html': render_markdown(body),
            'preview': make_preview(body),
            'source_file': str(md_file.relative_to(REPO_ROOT)),
            'url': f"/blog/{slug}/",
        })

    return posts


def check_collisions(posts):
    slug_to_sources = {}
    for post in posts:
        slug = post['slug']
        if slug not in slug_to_sources:
            slug_to_sources[slug] = []
        slug_to_sources[slug].append(post['source_file'])

    collisions = {slug: files for slug, files in slug_to_sources.items() if len(files) > 1}

    if not collisions:
        return

    lines = ["ERROR: The following source files would generate identical URLs:"]
    for slug, files in collisions.items():
        lines.append(f"  /blog/{slug}/")
        for f in files:
            lines.append(f"    - {f}")
    print('\n'.join(lines))
    sys.exit(1)


def collect_sections():
    sections = []
    for md_file in sorted(CONTENT_DIR.glob("*.md")):
        front_matter, _ = parse_md_file(md_file)
        section = front_matter.get('section', '').strip()
        if not section or section == 'blog':
            continue
        sections.append(section)
    return sections


def build_nav(sections):
    nav = [{'name': 'blog', 'url': '/blog/'}]
    for section in sections:
        nav.append({'name': section, 'url': f'/{section}/'})
    nav.append({'name': 'tal-player', 'url': '/tal-player/'})
    return nav


def generate_blog_posts(env, posts, nav):
    template = env.get_template('post.html')
    for post in posts:
        out_dir = OUTPUT_DIR / 'blog' / post['slug']
        out_dir.mkdir(parents=True, exist_ok=True)
        rendered = template.render(post=post, active='blog', nav=nav)
        (out_dir / 'index.html').write_text(rendered, encoding='utf-8')


def generate_blog_index(env, posts, nav):
    template = env.get_template('blog_index.html')
    sorted_posts = sorted(posts, key=lambda p: p['date'], reverse=True)
    rendered = template.render(posts=sorted_posts, active='blog', nav=nav)
    out_dir = OUTPUT_DIR / 'blog'
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'index.html').write_text(rendered, encoding='utf-8')
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / 'index.html').write_text(rendered, encoding='utf-8')


def generate_css():
    tailwindcss = Path(sys.executable).parent / "tailwindcss"
    input_css = GENERATOR_DIR / "input.css"
    output_css = OUTPUT_DIR / "assets" / "styles.css"
    (OUTPUT_DIR / "assets").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(tailwindcss), "-i", str(input_css), "-o", str(output_css), "--minify"],
        check=True,
        cwd=str(GENERATOR_DIR),
    )


def generate_tal_player(env, nav):
    template = env.get_template('tal_player.html')
    out_dir = OUTPUT_DIR / 'tal-player'
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = template.render(active='tal-player', nav=nav)
    (out_dir / 'index.html').write_text(rendered, encoding='utf-8')


def generate_section(env, section_name, front_matter, html, nav):
    template = env.get_template('section.html')
    out_dir = OUTPUT_DIR / section_name
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = template.render(
        title=front_matter.get('title', section_name.capitalize()),
        html=html,
        active=section_name,
        nav=nav,
    )
    (out_dir / 'index.html').write_text(rendered, encoding='utf-8')



def main():
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir()

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)

    posts = collect_posts()
    check_collisions(posts)

    sections = collect_sections()
    nav = build_nav(sections)

    generate_blog_posts(env, posts, nav)
    generate_blog_index(env, posts, nav)
    generate_tal_player(env, nav)

    if ASSETS_DIR.exists():
        shutil.copytree(ASSETS_DIR, OUTPUT_DIR / "assets")

    generate_css()

    section_count = 0
    for md_file in sorted((CONTENT_DIR).glob("*.md")):
        front_matter, body = parse_md_file(md_file)
        section = front_matter.get('section', '').strip()
        if not section or section == 'blog':
            continue
        html = render_markdown(body)
        generate_section(env, section, front_matter, html, nav)
        section_count += 1

    cname_src = REPO_ROOT / "CNAME"
    if cname_src.exists():
        shutil.copy(cname_src, OUTPUT_DIR / "CNAME")

    print(f"Done: {len(posts)} blog post(s), {section_count} section(s).")


if __name__ == '__main__':
    main()
