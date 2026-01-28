#!/usr/bin/env python3
"""
Summarize changes in documentation using Gemini API and update the blog.
"""

import os
import sys
import argparse
import google.generativeai as genai
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).parent.parent / 'docs'
ROOT_DIR = Path(__file__).parent.parent # Configuration
BLOG_FILE = ROOT_DIR / 'pages' / 'index.html'

def setup_gemini():
    """Configure Gemini API."""
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable not set.")
        sys.exit(1)
    
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-2.0-flash-lite')

import subprocess
import re

def get_git_diff(file_path, commit_hash=None):
# ... (omitted lines) ...

    """Get the git diff for a file."""
    try:
        if commit_hash:
            # Diff against the previous commit
            result = subprocess.run(
                ['git', 'diff', f'{commit_hash}^', commit_hash, '--', file_path],
                capture_output=True, text=True, check=False
            )
            return result.stdout
            
        # Check staged changes first
        result = subprocess.run(
            ['git', 'diff', '--cached', file_path],
            capture_output=True, text=True, check=False
        )
        if result.stdout.strip():
            return result.stdout
            
        # If no staged changes, check unstaged (for local testing)
        result = subprocess.run(
            ['git', 'diff', file_path],
            capture_output=True, text=True, check=False
        )
        return result.stdout
    except Exception as e:
        logger.error(f"Failed to get diff for {file_path}: {e}")
        return None

def slugify(text):
    """Create a slug from a header text."""
    # Simple slugify: lowercase, replace spaces with hyphens, remove non-alphanumeric (except hyphens)
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text

def generate_summary(model, filename, content, is_new=False):
    """Generate a summary using Gemini."""
    
    prompt_context = "This is a new file." if is_new else "Here is the git diff of the changes."
    
    task_instructions = """
    1. **CRITICAL: FILTER TRIVIAL CHANGES.** 
       - Ignore whitespace, typos, formatting (e.g. bold/italic changes), and simple rewording.
       - **Ignore code block attribute changes** (e.g. removing `theme={{null}}` or similar metadata).
       - Ignore internal meta-data updates or comment changes.
       - If the changes are trivial as described above: **RETURN AN EMPTY LIST []**.
    2. If the changes are meaningful:
       - If broad/many changes: Return ONE summary with header "Overview".
       - If specific changes: Return a list of summaries for each changed section (header).
    """

    if is_new:
        task_instructions = """
    1. **NEW FILE ADDED.**
       - Since this is a completely new file, do not break it down into sections.
       - **Return EXACTLY ONE summary** with the header "Overview".
       - The summary should describe the overall purpose and contents of this new file.
    """

    prompt = f"""
    You are a tech news editor. Analyze the changes in the "{filename}" documentation.
    {prompt_context}
    
    Task:
    {task_instructions}
    
    3. **Write informative properties.** The summary should explain "what changed" and "why it matters" in Korean. (Max 150 characters).
    4. Return the result in JSON format.
    
    Format example for TRIVIAL changes (RETURN THIS if changes are minor):
    []
    
    Format example for MEANINGFUL changes:
    [
        {{
            "header": "Overview", 
            "summary": "전반적인 내용이 재구성되었으며, 새로운 모범 사례 섹션이 추가되어 더 효율적인 워크플로우를 제안합니다."
        }}
    ]
    
    Content/Diff:
    {content[:10000]}
    """
    
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            return json.loads(response.text)
        except Exception as e:
            error_str = str(e)
            if "429" in error_str and attempt < max_retries - 1:
                logger.warning(f"Rate limit hit for {filename}. Retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
                continue
            
            if attempt == max_retries - 1:
                logger.error(f"Gemini API failed for {filename} after retries: {e}")
                
    # Fallback to single summary only if meaningful retry failed
    return [{"header": "Overview", "summary": f"{filename} 문서가 업데이트되었습니다."}]

BLOG_FILE = ROOT_DIR / 'index.html'
CHANGELOG_JSON = ROOT_DIR / 'pages' / 'changelog.json'

def load_changelog():
    if not CHANGELOG_JSON.exists():
        return []
    try:
        return json.loads(CHANGELOG_JSON.read_text(encoding='utf-8'))
    except:
        return []

def save_changelog(data):
    # Ensure pages dir exists
    CHANGELOG_JSON.parent.mkdir(exist_ok=True)
    CHANGELOG_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

def get_commit_date(commit_hash):
    """Get the commit date in ISO 8601 format."""
    try:
        # Get ISO 8601 timestamp (e.g., 2026-01-28T15:04:31+00:00)
        # using -I (strict ISO 8601) is safer
        iso_date = subprocess.check_output(
            ['git', 'show', '-s', '--format=%cI', commit_hash],
            text=True
        ).strip()
        return iso_date
    except Exception as e:
        logger.warning(f"Failed to get commit date for {commit_hash}: {e}")
        return datetime.now(timezone.utc).isoformat()

def update_json_data(updates, commit_hash=None):
    """
    Append new updates to changelog.json
    """
    if not updates:
        return load_changelog()
        
    history = load_changelog()
    
    if commit_hash:
        date_str = get_commit_date(commit_hash)
    else:
        date_str = datetime.now(timezone.utc).isoformat()
    
    # Create new entry block
    new_entry = {
        "date": date_str,
        "commit_hash": commit_hash,
        "entries": updates
    }
    
    # Prepend to history (newest first)
    history.insert(0, new_entry)
    
    save_changelog(history)
    return history

def format_date_kst(iso_date_str):
    """Convert ISO date string to KST (UTC+9) formatted string."""
    try:
        # Handle simple ISO format
        dt = datetime.fromisoformat(iso_date_str)
        # If naive, assume UTC (or local? Git usually gives offset)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            
        kst_offset = timedelta(hours=9)
        dt_kst = dt.astimezone(timezone(kst_offset))
        return dt_kst.strftime('%Y-%m-%d %H:%M')
    except Exception as e:
        logger.warning(f"Date parsing failed for {iso_date_str}: {e}")
        return iso_date_str

def render_blog_from_json(history):
    """
    Regenerate index.html fully from JSON history
    """
    html_header = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Claude Code Docs Changelog</title>
    <style>
        body { font-family: -apple-system, BlinkMacMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; background-color: #fafafa; }
        h1 { border-bottom: 2px solid #eee; padding-bottom: 10px; color: #111; }
        
        .group { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 30px; overflow: hidden; }
        .group-header { margin: 0; padding: 15px 20px; background: #f5f5f5; border-bottom: 1px solid #eee; font-size: 1.1em; color: #444; }
        .group-content { padding: 5px 0; }
        
        .entry { padding: 15px 20px; border-bottom: 1px solid #f0f0f0; }
        .entry:last-child { border-bottom: none; }
        
        .title { font-size: 1.1em; font-weight: 600; margin-bottom: 6px; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
        .title a { text-decoration: none; color: #2563eb; }
        .title a:hover { text-decoration: underline; }
        
        .summary { color: #555; font-size: 0.95em; line-height: 1.5; margin-left: 2px; }
        
        .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75em; font-weight: 700; color: white; line-height: 1.4; }
        .tag.new { background-color: #2e7d32; }   /* Green */
        .tag.update { background-color: #1976d2; } /* Blue */
        .tag.delete { background-color: #c62828; } /* Red */
        
        .footer { margin-top: 50px; font-size: 0.8em; color: #888; text-align: center; }
    </style>
</head>
<body>
    <h1>Claude Code Docs Changelog</h1>
    <p style="color: #666; margin-bottom: 30px;">Automated changelog tracked by Gemini.</p>
"""
    html_footer = """
    <div class="footer">
        Updated automatically by GitHub Actions
    </div>
</body>
</html>"""

    entries_html = ""
    
    for group in history:
        raw_date = group.get('date', '')
        commit_hash = group.get('commit_hash')
        entries = group.get('entries', [])
        
        # Format date for display
        display_date = format_date_kst(raw_date)
        
        if not entries:
            continue
            
        commit_link = ""
        if commit_hash:
            try:
                repo_url = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], text=True).strip()
                if 'git@' in repo_url:
                    repo_url = repo_url.replace(':', '/').replace('git@', 'https://')
                if repo_url.endswith('.git'):
                    repo_url = repo_url[:-4]
                commit_link_url = f"{repo_url}/commit/{commit_hash}"
                commit_link = f' <a href="{commit_link_url}" target="_blank" style="color: #999; text-decoration: none; font-family: monospace; font-size: 0.9em; margin-left: 10px;">{commit_hash}</a>'
            except:
                commit_link = f' <span style="color: #999; font-family: monospace; font-size: 0.9em; margin-left: 10px;">{commit_hash}</span>'

        entries_html += f"""
        <div class="group">
            <h3 class="group-header">{display_date}{commit_link}</h3>
            <div class="group-content">
        """
        
        for update in entries:
            entries_html += f"""
                <div class="entry">
                    <div class="title">
                        <span class="tag {update['tag_class']}">{update['tag_text']}</span>
                        {update['title']}
                    </div>
                    <div class="summary">{update['summary']}</div>
                </div>
            """
            
        entries_html += """
            </div>
        </div>
        """

    BLOG_FILE.write_text(html_header + entries_html + html_footer, encoding='utf-8')
    logger.info("Regenerated index.html from JSON history.")

import time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--files', nargs='+', required=True, help='List of changed files')
    parser.add_argument('--commit-hash', help='Short commit hash', default=None)
    args = parser.parse_args()
    
    if not args.files:
        logger.info("No files provided.")
        return

    model = setup_gemini()
    updates = []
    base_url = "https://code.claude.com/docs/en"
    
    for file_arg in args.files:
        # Rate limit safety
        time.sleep(1)
        
        # Parse "STATUS:FILENAME" or just "FILENAME"
        if ':' in file_arg:
            status, file_path = file_arg.split(':', 1)
        else:
            status, file_path = 'M', file_arg # Default to modify
            
        filename = os.path.basename(file_path)
        if not filename.endswith('.md'):
            continue
            
        logger.info(f"Processing {filename} (Status: {status})...")
        
        # Determine tag and style
        tag_text = "UPDATE"
        tag_class = "update"
        
        if status == 'A':
            tag_text = "NEW"
            tag_class = "new"
        elif status == 'D':
            tag_text = "DELETE"
            tag_class = "delete"
        
        # Determine content source
        is_new = (status == 'A')
        content = ""
        
        if status == 'D':
            # Deleted file: Simple single entry
            updates.append({
                'title': filename.replace('.md', '').title(), # No link
                'summary': "문서가 삭제되었습니다.",
                'tag_text': tag_text,
                'tag_class': tag_class
            })
            continue
        elif status == 'A':
            content = get_git_diff(file_path, args.commit_hash)
            # get_file_diff currently does 'git diff', which might be empty for new files if not added.
            # But the workflow does 'git add'.
            # For new files, valid approach is to read content.
            if not content:
                 try:
                    if args.commit_hash:
                         # Read from specific commit
                         content = subprocess.check_output(['git', 'show', f'{args.commit_hash}:{file_path}'], text=True)
                    else:
                        content = Path(file_path).read_text(encoding='utf-8')
                 except: 
                    content = ""
        else:
             content = get_git_diff(file_path, args.commit_hash)
             
        if not content:
            logger.warning(f"No content found for {filename}")
            continue

        # Generate granular summaries
        summaries = generate_summary(model, filename, content, is_new)
        
        for item in summaries:
            header = item.get('header', 'Overview')
            summary_text = item.get('summary', '')
            
            # File basename for URL (remove extension)
            file_basename = filename.replace('.md', '')
            
            # Construct Deep Link
            # If header is "Overview" or "File Title", link to top
            if header.lower() in ['overview', file_basename.lower(), filename.lower(), '']:
                url = f"{base_url}/{file_basename}"
                display_title = f"{file_basename.title().replace('-', ' ')}"
            else:
                slug = slugify(header)
                url = f"{base_url}/{file_basename}#{slug}"
                display_title = f"{file_basename.title().replace('-', ' ')} > {header}"
                
            updates.append({
                'title': f'<a href="{url}" target="_blank">{display_title}</a>',
                'summary': summary_text,
                'tag_text': tag_text,
                'tag_class': tag_class
            })
            
    if updates:
        # 1. Update JSON Data
        history = update_json_data(updates, args.commit_hash)
        # 2. Render HTML from new Data
        render_blog_from_json(history)


if __name__ == '__main__':
    main()
