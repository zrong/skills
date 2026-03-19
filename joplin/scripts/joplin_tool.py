#!/usr/bin/env python3
import os
import sys
import json
import click
import httpx
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional, Dict, Any, List, Union

# Load .env from project root
dotenv_path = Path(__file__).parents[2] / ".env"
load_dotenv(dotenv_path)

JOPLIN_TOKEN = os.getenv("JOPLIN_TOKEN")
JOPLIN_BASE_URL = os.getenv("JOPLIN_BASE_URL", "http://localhost:41184")

class JoplinClient:
    def __init__(self, token: str, base_url: str):
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, 
                 json_data: Optional[Dict[str, Any]] = None, files: Optional[Dict[str, Any]] = None,
                 return_json: bool = True, return_raw: bool = False):
        if not self.token:
            click.echo("Error: JOPLIN_TOKEN not found in .env", err=True)
            sys.exit(1)
            
        params = params or {}
        params["token"] = self.token
        url = f"{self.base_url}/{path.lstrip('/')}"
        
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.request(method, url, params=params, json=json_data, files=files)
                response.raise_for_status()
                
                if return_raw:
                    return response.content
                if not response.content:
                    return {}
                if return_json:
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        return {"content": response.text}
                return response.text
        except httpx.ConnectError:
            click.echo(f"Error: Could not connect to Joplin API at {self.base_url}. Is Joplin running with the Clipper service enabled?", err=True)
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            click.echo(f"HTTP Error {e.response.status_code}: {e.response.text}", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"Unexpected error: {str(e)}", err=True)
            sys.exit(1)

    # --- Global ---
    def ping(self):
        return self._request("GET", "ping", return_json=False)

    def events(self, cursor: Optional[int] = None, limit: int = 100):
        params = {"limit": limit}
        if cursor: params["cursor"] = cursor
        return self._request("GET", "events", params=params)

    # --- Notes ---
    def list_notes(self, fields: str = "id,title", limit: int = 10, page: int = 1, 
                   order_by: str = "updated_time", order_dir: str = "DESC"):
        params = {"fields": fields, "limit": limit, "page": page, "order_by": order_by, "order_dir": order_dir}
        return self._request("GET", "notes", params=params)

    def get_note(self, note_id: str, fields: str = "id,title,body,parent_id,created_time,updated_time"):
        return self._request("GET", f"notes/{note_id}", params={"fields": fields})

    def create_note(self, title: str, body: str, parent_id: Optional[str] = None, **kwargs):
        data = {"title": title, "body": body}
        if parent_id: data["parent_id"] = parent_id
        data.update(kwargs)
        return self._request("POST", "notes", json_data=data)

    def update_note(self, note_id: str, **kwargs):
        return self._request("PUT", f"notes/{note_id}", json_data=kwargs)

    def delete_note(self, note_id: str):
        return self._request("DELETE", f"notes/{note_id}")

    def get_note_tags(self, note_id: str):
        return self._request("GET", f"notes/{note_id}/tags")

    def get_note_resources(self, note_id: str):
        return self._request("GET", f"notes/{note_id}/resources")

    # --- Folders (Notebooks) ---
    def list_folders(self, fields: str = "id,title,parent_id", limit: int = 100, page: int = 1):
        params = {"fields": fields, "limit": limit, "page": page}
        return self._request("GET", "folders", params=params)

    def get_folder(self, folder_id: str):
        return self._request("GET", f"folders/{folder_id}")

    def create_folder(self, title: str, parent_id: Optional[str] = None):
        data = {"title": title}
        if parent_id: data["parent_id"] = parent_id
        return self._request("POST", "folders", json_data=data)

    def update_folder(self, folder_id: str, title: str):
        return self._request("PUT", f"folders/{folder_id}", json_data={"title": title})

    def delete_folder(self, folder_id: str):
        return self._request("DELETE", f"folders/{folder_id}")

    def get_folder_notes(self, folder_id: str):
        return self._request("GET", f"folders/{folder_id}/notes")

    # --- Tags ---
    def list_tags(self):
        return self._request("GET", "tags")

    def get_tag(self, tag_id: str):
        return self._request("GET", f"tags/{tag_id}")

    def create_tag(self, title: str):
        return self._request("POST", "tags", json_data={"title": title})

    def update_tag(self, tag_id: str, title: str):
        return self._request("PUT", f"tags/{tag_id}", json_data={"title": title})

    def delete_tag(self, tag_id: str):
        return self._request("DELETE", f"tags/{tag_id}")

    def add_tag_to_note(self, tag_id: str, note_id: str):
        return self._request("POST", f"tags/{tag_id}/notes", json_data={"id": note_id})

    def remove_tag_from_note(self, tag_id: str, note_id: str):
        return self._request("DELETE", f"tags/{tag_id}/notes/{note_id}")

    def get_tag_notes(self, tag_id: str):
        return self._request("GET", f"tags/{tag_id}/notes")

    # --- Resources (Attachments) ---
    def list_resources(self, fields: str = "id,title,file_extension,size", limit: int = 100):
        return self._request("GET", "resources", params={"fields": fields, "limit": limit})

    def get_resource(self, resource_id: str):
        return self._request("GET", f"resources/{resource_id}")

    def upload_resource(self, file_path: str, title: Optional[str] = None):
        p = Path(file_path)
        if not p.exists():
            click.echo(f"Error: File {file_path} does not exist", err=True)
            sys.exit(1)
        
        props = {"title": title or p.name}
        files = {
            "data": (p.name, open(file_path, "rb")),
            "props": (None, json.dumps(props))
        }
        return self._request("POST", "resources", files=files)

    def download_resource(self, resource_id: str):
        return self._request("GET", f"resources/{resource_id}/file", return_raw=True)

    def delete_resource(self, resource_id: str):
        return self._request("DELETE", f"resources/{resource_id}")

    # --- Revisions ---
    def list_revisions(self):
        return self._request("GET", "revisions")

    def get_revision(self, rev_id: str):
        return self._request("GET", f"revisions/{rev_id}")

    # --- Search ---
    def search(self, query: str, type: str = "note", limit: int = 10, page: int = 1):
        params = {"query": query, "type": type, "limit": limit, "page": page}
        return self._request("GET", "search", params=params)

# --- CLI Implementation ---

@click.group()
def cli():
    """Complete Joplin REST API CLI Tool."""
    pass

@cli.command()
def ping():
    """Check connection."""
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(client.ping())

@cli.command()
@click.option("--cursor", type=int)
@click.option("--limit", default=100)
def events(cursor, limit):
    """List events."""
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.events(cursor, limit), indent=2))

@cli.group()
def note():
    """Note operations."""
    pass

@note.command(name="list")
@click.option("--limit", "-l", default=10)
@click.option("--page", "-p", default=1)
@click.option("--fields", "-f", default="id,title")
@click.option("--order-by", default="updated_time")
@click.option("--order-dir", default="DESC")
def note_list(limit, page, fields, order_by, order_dir):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.list_notes(fields, limit, page, order_by, order_dir), indent=2))

@note.command(name="get")
@click.argument("id")
def note_get(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.get_note(id), indent=2))

@note.command(name="create")
@click.option("--title", "-t", required=True)
@click.option("--body", "-b", default="")
@click.option("--parent", "-p", help="Folder ID")
def note_create(title, body, parent):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.create_note(title, body, parent), indent=2))

@note.command(name="update")
@click.argument("id")
@click.option("--title", "-t")
@click.option("--body", "-b")
@click.option("--parent", "-p")
def note_update(id, title, body, parent):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    kwargs = {}
    if title: kwargs["title"] = title
    if body: kwargs["body"] = body
    if parent: kwargs["parent_id"] = parent
    click.echo(json.dumps(client.update_note(id, **kwargs), indent=2))

@note.command(name="delete")
@click.argument("id")
def note_delete(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    client.delete_note(id)
    click.echo(f"Note {id} deleted.")

@note.command(name="tags")
@click.argument("id")
def note_tags(id):
    """List tags for a note."""
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.get_note_tags(id), indent=2))

@note.command(name="resources")
@click.argument("id")
def note_resources(id):
    """List resources for a note."""
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.get_note_resources(id), indent=2))

@cli.group()
def folder():
    """Folder (Notebook) operations."""
    pass

@folder.command(name="list")
@click.option("--fields", default="id,title,parent_id")
def folder_list(fields):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.list_folders(fields=fields), indent=2))

@folder.command(name="get")
@click.argument("id")
def folder_get(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.get_folder(id), indent=2))

@folder.command(name="create")
@click.argument("title")
@click.option("--parent", "-p")
def folder_create(title, parent):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.create_folder(title, parent), indent=2))

@folder.command(name="update")
@click.argument("id")
@click.argument("title")
def folder_update(id, title):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.update_folder(id, title), indent=2))

@folder.command(name="delete")
@click.argument("id")
def folder_delete(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    client.delete_folder(id)
    click.echo(f"Folder {id} deleted.")

@cli.group()
def tag():
    """Tag operations."""
    pass

@tag.command(name="list")
def tag_list():
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.list_tags(), indent=2))

@tag.command(name="get")
@click.argument("id")
def tag_get(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.get_tag(id), indent=2))

@tag.command(name="create")
@click.argument("title")
def tag_create(title):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.create_tag(title), indent=2))

@tag.command(name="update")
@click.argument("id")
@click.argument("title")
def tag_update(id, title):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.update_tag(id, title), indent=2))

@tag.command(name="delete")
@click.argument("id")
def tag_delete(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    client.delete_tag(id)
    click.echo(f"Tag {id} deleted.")

@tag.command(name="add")
@click.argument("tag_id")
@click.argument("note_id")
def tag_add(tag_id, note_id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.add_tag_to_note(tag_id, note_id), indent=2))

@tag.command(name="remove")
@click.argument("tag_id")
@click.argument("note_id")
def tag_remove(tag_id, note_id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    client.remove_tag_from_note(tag_id, note_id)
    click.echo(f"Tag {tag_id} removed from note {note_id}")

@cli.group()
def resource():
    """Resource (Attachment) operations."""
    pass

@resource.command(name="list")
@click.option("--limit", default=100)
def res_list(limit):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.list_resources(limit=limit), indent=2))

@resource.command(name="get")
@click.argument("id")
def res_get(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.get_resource(id), indent=2))

@resource.command(name="upload")
@click.argument("file_path")
@click.option("--title", "-t")
def res_upload(file_path, title):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.upload_resource(file_path, title), indent=2))

@resource.command(name="download")
@click.argument("id")
@click.argument("dest")
def res_download(id, dest):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    content = client.download_resource(id)
    with open(dest, "wb") as f:
        f.write(content)
    click.echo(f"Resource {id} saved to {dest}")

@resource.command(name="delete")
@click.argument("id")
def res_delete(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    client.delete_resource(id)
    click.echo(f"Resource {id} deleted.")

@cli.group()
def revision():
    """Revision operations."""
    pass

@revision.command(name="list")
def rev_list():
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.list_revisions(), indent=2))

@revision.command(name="get")
@click.argument("id")
def rev_get(id):
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.get_revision(id), indent=2))

@cli.command()
@click.option("--query", "-q", required=True)
@click.option("--type", "-t", default="note")
@click.option("--limit", default=10)
@click.option("--page", default=1)
def search(query, type, limit, page):
    """Search query."""
    client = JoplinClient(JOPLIN_TOKEN, JOPLIN_BASE_URL)
    click.echo(json.dumps(client.search(query, type, limit, page), indent=2))

if __name__ == "__main__":
    cli()
