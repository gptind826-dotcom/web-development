from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime
import zipfile
import os
import shutil
import uuid
import sqlite3
import threading
import http.server
import socketserver

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create directories
os.makedirs("templates", exist_ok=True)
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
    conn.commit()
    conn.close()

init_db()

sites_data = {}
base_port = 8080

def find_index_file(folder_path):
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file == 'index.html':
                return os.path.join(root, file)
    return None

def run_server(site_id, folder_path, port):
    os.chdir(folder_path)
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        sites_data[site_id] = {"server": httpd, "port": port}
        httpd.serve_forever()

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
            "created_at": site[5]
        })
    
    return {"sites": sites_list}

@app.post("/api/deploy")
async def deploy_site(file: UploadFile = File(...), site_name: str = Form(...)):
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
            os.remove(zip_path)
            shutil.rmtree(extract_path)
            raise HTTPException(400, "No index.html found in ZIP")
        
        port = base_port + len(os.listdir("sites"))
        domain = f"{site_name.lower().replace(' ', '-')}.localhost"
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sites (id, name, domain, status, port, created_at, zip_path, folder_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (site_id, site_name, domain, "stopped", port, datetime.now().isoformat(), zip_path, extract_path)
        )
        conn.commit()
        conn.close()
        
        return {"success": True, "site_id": site_id, "domain": domain, "port": port}
    
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/site/{site_id}/start")
async def start_site(site_id: str):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT folder_path, port FROM sites WHERE id = ?", (site_id,))
    site = cursor.fetchone()
    conn.close()
    
    if not site:
        raise HTTPException(404, "Site not found")
    
    folder_path, port = site
    
    if site_id in sites_data:
        return {"message": "Already running"}
    
    thread = threading.Thread(target=run_server, args=(site_id, folder_path, port), daemon=True)
    thread.start()
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE sites SET status = 'running' WHERE id = ?", (site_id,))
    conn.commit()
    conn.close()
    
    return {"message": f"Site started on port {port}"}

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
    import time
    time.sleep(1)
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

if __name__ == "__main__":
    import uvicorn
    import os
    
    # Get PORT from environment variable (Render sets this)
    port = int(os.environ.get("PORT", 10000))
    
    print("\n" + "="*50)
    print("🚀 WebHost Platform Started on Render")
    print("="*50)
    print(f"📍 Running on port: {port}")
    print("="*50 + "\n")
    
    # Bind to 0.0.0.0 as required by Render
    uvicorn.run(app, host="0.0.0.0", port=port)
