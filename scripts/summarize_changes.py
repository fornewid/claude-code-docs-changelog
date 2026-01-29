#!/usr/bin/env python3
"""
Summarize changes in documentation using Gemini API and update the blog.
"""

import os
import sys
import argparse
import argparse
from google import genai
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
    
    return genai.Client(api_key=api_key)

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

def generate_summary(client, filename, content, is_new=False):
    """Generate a summary using Gemini."""
    
    prompt_context = "This is a new file." if is_new else "Here is the git diff of the changes."
    
    task_instructions = """
    1. **CRITICAL: FILTER TRIVIAL CHANGES.** 
       - Ignore whitespace, typos, formatting (e.g. bold/italic changes), and simple rewording.
       - **Ignore code block attribute changes** (e.g. removing `theme={{null}}` or similar metadata).
       - Ignore internal meta-data updates or comment changes.
       - If the changes are trivial as described above: **RETURN AN EMPTY LIST []**.
    2. If the changes are meaningful:
       - **Return EXACTLY ONE summary** that consolidates all changes in the file.
       - Use "Overview" as the header.
       - Do not split into multiple items based on sections.
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
            response = client.models.generate_content(
                model='gemini-2.0-flash-lite',
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
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

import time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--files', nargs='+', required=True, help='List of changed files')
    parser.add_argument('--commit-hash', help='Short commit hash', default=None)
    args = parser.parse_args()
    
    if not args.files:
        logger.info("No files provided.")
        return

    client = setup_gemini()
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
            if not content:
                 try:
                    if args.commit_hash:
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
        summaries = generate_summary(client, filename, content, is_new)
        
        for item in summaries:
            header = item.get('header', 'Overview')
            summary_text = item.get('summary', '')
            
            file_basename = filename.replace('.md', '')
            
            if header.lower() in ['overview', file_basename.lower(), filename.lower(), '']:
                url = f"{base_url}/{file_basename}"
                display_title = f"{file_basename.title().replace('-', ' ')}"
            else:
                slug = slugify(header)
                url = f"{base_url}/{file_basename}#{slug}"
                display_title = f"{file_basename.title().replace('-', ' ')} > {header}"
                
            entry = {
                'title': f'<a href="{url}" target="_blank">{display_title}</a>',
                'summary': summary_text,
                'tag_text': tag_text,
                'tag_class': tag_class
            }
            
            # Include diff only if it's an UPDATE (Modified)
            if tag_class == 'update' and content:
                # Save diff to separate file to keep changelog.json light
                diffs_dir = ROOT_DIR / 'pages' / 'diffs'
                diffs_dir.mkdir(parents=True, exist_ok=True)
                
                # Create safe filename: commit_hash (or generic) + filename
                safe_hash = args.commit_hash if args.commit_hash else 'local'
                diff_filename = f"{safe_hash}_{file_basename}.txt"
                diff_path = diffs_dir / diff_filename
                
                diff_path.write_text(content, encoding='utf-8')
                
                # Store relative path for frontend to fetch
                entry['diff_file'] = f"pages/diffs/{diff_filename}"
                
            updates.append(entry)
            
    if updates:
        # 2. Update JSON Data (No HTML rendering)
        history = update_json_data(updates, args.commit_hash)
        
        # 3. Generate Release Body
        release_body_path = ROOT_DIR / 'release_body.md'
        release_content = "## Documentation Updates\\n\\n"
        
        for update in updates:
            tag = f"[{update['tag_text']}]"
            title = update['title']
            if '<a' in title:
                match = re.search(r'>([^<]+)<', title)
                if match:
                    title = match.group(1)
            
            summary = update['summary']
            
            release_content += f"### {tag} {title}\\n"
            release_content += f"{summary}\\n\\n"
            
        release_body_path.write_text(release_content, encoding='utf-8')
        logger.info(f"Generated release body at {release_body_path}")


if __name__ == '__main__':
    main()
