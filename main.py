from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from datetime import datetime
import subprocess
import zipfile
import os
import shutil
import uuid
import json
import sqlite3
import asyncio
from pathlib import Path

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("sites", exist_ok=True)
os.makedirs("uploads", exist_ok=True)

templates = Jinja2Templates(directory="templates")

def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sites (
            id TEXT PRIMARY KEY,
            name TEXT,
            domain TEXT,
            status TEXT,
            port INTEGER,
            created_at TEXT,
            zip_path TEXT,
            folder_path TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY,
            site_id TEXT,
            version TEXT,
            deployed_at TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

sites_data = {}
base_port = 3000

def find_index_file(folder_path):
    possible_files = ['index.html', 'index.htm', 'default.html', 'home.html']
    for file in possible_files:
        full_path = os.path.join(folder_path, file)
        if os.path.exists(full_path):
            return full_path
    
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file in possible_files:
                return os.path.join(root, file)
    
    html_files = [f for f in os.listdir(folder_path) if f.endswith('.html')]
    if html_files:
        return os.path.join(folder_path, html_files[0])
    
    return None

def serve_site(site_id, folder_path, port):
    import http.server
    import socketserver
    import threading
    
    os.chdir(folder_path)
    
    handler = http.server.SimpleHTTPRequestHandler
    
    with socketserver.TCPServer(("", port), handler) as httpd:
        sites_data[site_id] = {
            "server": httpd,
            "port": port,
            "thread": threading.current_thread()
        }
        httpd.serve_forever()

def start_site_server(site_id, folder_path, port):
    thread = threading.Thread(target=serve_site, args=(site_id, folder_path, port), daemon=True)
    thread.start()
    return thread

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sites ORDER BY created_at DESC")
    sites = cursor.fetchall()
    conn.close()
    
    sites_list = []
    for site in sites:
        sites_list.append({
            "id": site[0],
            "name": site[1],
            "domain": site[2],
            "status": site[3],
            "port": site[4],
            "created_at": site[5]
        })
    
    return templates.TemplateResponse("dashboard.html", {"request": request, "sites": sites_list})

@app.get("/deploy", response_class=HTMLResponse)
async def deploy_page(request: Request):
    return templates.TemplateResponse("deploy.html", {"request": request})

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sites")
    sites = cursor.fetchall()
    conn.close()
    
    sites_list = []
    for site in sites:
        sites_list.append({
            "id": site[0],
            "name": site[1],
            "domain": site[2],
            "status": site[3],
            "port": site[4],
            "created_at": site[5]
        })
    
    return templates.TemplateResponse("admin.html", {"request": request, "sites": sites_list})

@app.post("/api/deploy")
async def deploy_site(
    file: UploadFile = File(...),
    site_name: str = Form(...),
    custom_domain: str = Form(None)
):
    try:
        site_id = str(uuid.uuid4())[:8]
        
        zip_path = os.path.join("uploads", f"{site_id}.zip")
        content = await file.read()
        with open(zip_path, "wb") as f:
            f.write(content)
        
        extract_path = os.path.join("sites", site_id)
        os.makedirs(extract_path, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
        
        index_file = find_index_file(extract_path)
        if not index_file:
            raise HTTPException(400, "No index.html file found in ZIP")
        
        port = base_port + len(os.listdir("sites"))
        
        domain = custom_domain if custom_domain else f"{site_name.lower().replace(' ', '-')}.localhost"
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sites (id, name, domain, status, port, created_at, zip_path, folder_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (site_id, site_name, domain, "stopped", port, datetime.now().isoformat(), zip_path, extract_path)
        )
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "site_id": site_id,
            "domain": domain,
            "port": port,
            "message": "Site deployed successfully"
        }
    
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/site/{site_id}/start")
async def start_site(site_id: str):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT folder_path, port, name FROM sites WHERE id = ?", (site_id,))
    site = cursor.fetchone()
    conn.close()
    
    if not site:
        raise HTTPException(404, "Site not found")
    
    folder_path, port, name = site
    
    if site_id in sites_data:
        return {"message": "Site already running"}
    
    try:
        start_site_server(site_id, folder_path, port)
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE sites SET status = 'running' WHERE id = ?", (site_id,))
        conn.commit()
        conn.close()
        
        return {"message": f"Site {name} started on port {port}"}
    
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/site/{site_id}/stop")
async def stop_site(site_id: str):
    if site_id in sites_data:
        try:
            sites_data[site_id]["server"].shutdown()
            del sites_data[site_id]
        except:
            pass
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE sites SET status = 'stopped' WHERE id = ?", (site_id,))
    conn.commit()
    conn.close()
    
    return {"message": "Site stopped"}

@app.post("/api/site/{site_id}/restart")
async def restart_site(site_id: str):
    await stop_site(site_id)
    await asyncio.sleep(1)
    return await start_site(site_id)

@app.delete("/api/site/{site_id}")
async def delete_site(site_id: str):
    await stop_site(site_id)
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT zip_path, folder_path FROM sites WHERE id = ?", (site_id,))
    site = cursor.fetchone()
    
    if site:
        zip_path, folder_path = site
        if os.path.exists(zip_path):
            os.remove(zip_path)
        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
    
    cursor.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    conn.commit()
    conn.close()
    
    return {"message": "Site deleted"}

@app.post("/api/site/{site_id}/deploy-new")
async def deploy_new_version(site_id: str, file: UploadFile = File(...)):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT folder_path, name FROM sites WHERE id = ?", (site_id,))
    site = cursor.fetchone()
    conn.close()
    
    if not site:
        raise HTTPException(404, "Site not found")
    
    folder_path, name = site
    
    await stop_site(site_id)
    
    shutil.rmtree(folder_path)
    os.makedirs(folder_path, exist_ok=True)
    
    content = await file.read()
    with zipfile.ZipFile(file.file, 'r') as zip_ref:
        zip_ref.extractall(folder_path)
    
    index_file = find_index_file(folder_path)
    if not index_file:
        raise HTTPException(400, "No index.html found in new version")
    
    await start_site(site_id)
    
    return {"message": "New version deployed successfully"}

@app.get("/api/sites")
async def get_sites():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, domain, status, port, created_at FROM sites")
    sites = cursor.fetchall()
    conn.close()
    
    sites_list = []
    for site in sites:
        sites_list.append({
            "id": site[0],
            "name": site[1],
            "domain": site[2],
            "status": site[3],
            "port": site[4],
            "created_at": site[5],
            "url": f"http://localhost:{site[4]}"
        })
    
    return {"sites": sites_list}

@app.get("/api/stats")
async def get_stats():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sites")
    total_sites = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM sites WHERE status = 'running'")
    running_sites = cursor.fetchone()[0]
    conn.close()
    
    return {
        "total_sites": total_sites,
        "running_sites": running_sites,
        "stopped_sites": total_sites - running_sites
    }

@app.get("/site/{site_id}")
async def view_site(site_id: str):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, port FROM sites WHERE id = ?", (site_id,))
    site = cursor.fetchone()
    conn.close()
    
    if not site:
        raise HTTPException(404, "Site not found")
    
    status, port = site
    if status != "running":
        raise HTTPException(400, "Site is not running")
    
    return JSONResponse({"url": f"http://localhost:{port}"})

if __name__ == "__main__":
    import uvicorn
    import threading
    print("=" * 50)
    print("Web Hosting Platform Started")
    print("=" * 50)
    print("Main Interface: http://localhost:8000")
    print("Dashboard: http://localhost:8000/dashboard")
    print("Deploy: http://localhost:8000/deploy")
    print("Admin: http://localhost:8000/admin")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)