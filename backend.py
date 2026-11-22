import os, sys, io, re, json, mimetypes, base64, shlex, zipfile, shutil, subprocess, threading, traceback
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

_real_run = subprocess.run
_real_co = subprocess.check_output
_real_system = os.system

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("ANDROID_PRIVATE", HERE)
LIBS = os.path.join(HOME, "libs")
WORK = os.path.join(HOME, "work")
CFG = os.path.join(HOME, "config.json")
PORT = 8765
PROJ = os.path.join(HOME, "projects")
for d in (LIBS, WORK, PROJ):
    os.makedirs(d, exist_ok=True)
sys.path.insert(0, LIBS)


def cfg():
    c = {"ollama": "http://127.0.0.1:11434", "mcp": [], "engine": "ollama",
         "api_base": "https://coarena.ai/api/v1", "api_key": "", "api_model": ""}
    try:
        c.update(json.load(open(CFG)))
    except Exception:
        pass
    return c


def save_cfg(c):
    json.dump(c, open(CFG, "w"))


def http(url, body=None, timeout=300):
    h = {"Content-Type": "application/json"} if body is not None else {}
    data = json.dumps(body).encode() if body is not None else None
    return urlopen(Request(url, data, h), timeout=timeout).read()


# ---------- paket yoneticisi (pip yok, saf Python wheel) ----------
def norm(n):
    return re.sub(r"[-_.]+", "_", n).lower()


def install(pkg, seen=None):
    seen = seen if seen is not None else set()
    if norm(pkg) in seen:
        return True
    seen.add(norm(pkg))
    info = json.loads(http("https://pypi.org/pypi/%s/json" % pkg, timeout=30))
    for f in info["urls"]:
        if f["filename"].endswith("py3-none-any.whl"):
            z = zipfile.ZipFile(io.BytesIO(http(f["url"], timeout=120)))
            z.extractall(LIBS)
            for r in info["info"].get("requires_dist") or []:
                if "extra ==" in r:
                    continue
                name = re.split(r"[ ;<>=!~\[(]", r)[0]
                try:
                    install(name, seen)
                except Exception:
                    pass
            return True
    return False


def clean():
    used = set()
    for f in os.listdir(WORK):
        if f.endswith(".py"):
            src = open(os.path.join(WORK, f), errors="ignore").read()
            used |= {norm(x) for x in re.findall(r"^\s*(?:import|from)\s+([A-Za-z0-9_]+)", src, re.M)}
    # kullanilanlarin bagimliliklarini da tut
    keep = set(used)
    for n in os.listdir(LIBS):
        if n.endswith(".dist-info"):
            name = norm(n.rsplit("-", 1)[0])
            if name in used:
                try:
                    meta = open(os.path.join(LIBS, n, "METADATA"), errors="ignore").read()
                    for r in re.findall(r"^Requires-Dist:\s*([A-Za-z0-9_.\-]+)", meta, re.M):
                        keep.add(norm(r))
                except Exception:
                    pass
    removed = []
    for n in os.listdir(LIBS):
        p = os.path.join(LIBS, n)
        if n.endswith((".dist-info", ".data")) or n in ("__pycache__", "bin"):
            continue
        base = norm(n[:-3] if n.endswith(".py") else n)
        if base not in keep:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            removed.append(base)
    for n in os.listdir(LIBS):
        if n.endswith(".dist-info") and norm(n.rsplit("-", 1)[0]) in removed:
            shutil.rmtree(os.path.join(LIBS, n), ignore_errors=True)
    return "Silinen: " + (", ".join(removed) if removed else "yok")


# ---------- model ----------
# ---------- ucretsiz API motoru (OpenAI uyumlu) ----------
def http_h(url, body=None, key="", timeout=120):
    h = {"Content-Type": "application/json", "User-Agent": "ajan/0.1"}
    if key:
        h["Authorization"] = "Bearer " + key
    data = json.dumps(body).encode() if body is not None else None
    return urlopen(Request(url, data, h), timeout=timeout).read()


def _num(x):
    try:
        return float(x)
    except Exception:
        return None


def is_free(m):
    mid = str(m.get("id", ""))
    if mid.endswith(":free"):
        return True
    if m.get("free") is True or str(m.get("tier", "")).lower() == "free":
        return True
    p = m.get("pricing")
    if isinstance(p, dict) and p:
        vals = [_num(v) for v in p.values() if _num(v) is not None]
        return bool(vals) and all(v == 0 for v in vals)
    return False


def has_price_info(m):
    return "free" in m or "tier" in m or isinstance(m.get("pricing"), dict)


SKIP = ("embed", "whisper", "tts", "image", "guard", "safety", "moderation", "rerank")
FREE = {"t": 0, "ids": [], "why": ""}


def api_free_ids(force=False):
    import time
    if not force and FREE["ids"] and time.time() - FREE["t"] < 600:
        return FREE["ids"]
    c = cfg()
    data = json.loads(http_h(c["api_base"].rstrip("/") + "/models", None, c["api_key"], 30))
    models = [m for m in data.get("data", data if isinstance(data, list) else [])
              if isinstance(m, dict) and not any(s in str(m.get("id", "")).lower() for s in SKIP)]
    free = [m for m in models if is_free(m)]
    if free:
        why = "%d ucretsiz model (toplam %d)" % (len(free), len(models))
    elif models and not any(has_price_info(m) for m in models):
        free, why = models, "fiyat bilgisi yok, hepsi ucretsiz sayildi (%d model)" % len(models)
    else:
        why = "ucretsiz model bulunamadi"
    FREE.update(t=time.time(), ids=[m["id"] for m in free], why=why)
    return FREE["ids"]


def api_chat(msgs):
    c = cfg()
    ids = list(api_free_ids())
    if c.get("api_model") in ids:
        ids.remove(c["api_model"])
        ids.insert(0, c["api_model"])
    if not ids:
        raise Exception("ucretsiz model yok (anahtar dogru mu?)")
    last = ""
    for mid in ids[:5]:
        try:
            r = json.loads(http_h(c["api_base"].rstrip("/") + "/chat/completions",
                                  {"model": mid, "messages": msgs}, c["api_key"], 120))
            t = r["choices"][0]["message"]["content"]
            return re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
        except Exception as e:
            last = "%s: %s" % (mid, e)
    raise Exception("API cevap vermedi, son hata: " + last)


def cfg_set(d):
    ok = {k: d[k] for k in ("ollama", "engine", "api_base", "api_key", "api_model") if k in d}
    save_cfg(dict(cfg(), **ok))
    FREE["t"] = 0
    return {"msg": "Kaydedildi" + (" (motor: %s)" % ok["engine"] if "engine" in ok else "")}


def api_models_route(d):
    try:
        ids = api_free_ids(True)
        return {"msg": "%s: %s" % (FREE["why"], ", ".join(ids[:6]))}
    except Exception as e:
        return {"msg": "Model listesi alinamadi: %s" % e}


def llm(msgs, model):
    if cfg().get("engine") == "api":
        return api_chat(msgs)
    return ollama_chat(msgs, model)


def ollama_chat(msgs, model):
    r = json.loads(http(cfg()["ollama"].rstrip("/") + "/api/chat",
                        {"model": model, "messages": msgs, "stream": False}))
    t = r["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()


def pull(name):
    http(cfg()["ollama"].rstrip("/") + "/api/pull", {"name": name, "stream": False}, timeout=3600)
    return True


# ---------- kod calistirma + kendini onarma ----------
def run_code(src, secs=25):
    buf, res = io.StringIO(), {}

    def t():
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                exec(compile(src, "ajan_kod", "exec"), {"__name__": "__main__", "git": _git_tool, "sh": _sh_tool, "WORK": WORK, "PROJ": PROJ, "mcp": _mcp_tool})
            res["ok"] = True
        except BaseException:
            res["ok"] = False
            buf.write(traceback.format_exc())

    th = threading.Thread(target=t, daemon=True)
    th.start()
    th.join(secs)
    if th.is_alive():
        return False, buf.getvalue() + "\nZaman asimi"
    return res.get("ok", False), buf.getvalue()


def code_of(text):
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return m.group(1) if m else None


SYS = ("Sen bir Python ajanisin. Gorevi yapan TAM kodu tek bir ```python blogunda ver. "
       "Sadece standart kutuphane veya saf Python paketleri kullan. Kisa yaz. "
       "Hazir fonksiyonlar (string dondurur, print ile yaz): "
       "git('clone https://github.com/kullanici/proje'), git('pull proje'), git('push proje mesaj'), "
       "sh('ls'). Degiskenler: WORK (dosya yazma klasoru), PROJ (indirilen projeler klasoru).")
COUNT = [0]


def agent(task, model, steps):
    msgs = [{"role": "system", "content": SYS + mcp_desc()}, {"role": "user", "content": task}]
    reply = ""
    for i in range(5):
        reply = llm(msgs, model)
        code = code_of(reply)
        if not code:
            return reply
        steps.append("kod calistiriliyor (deneme %d)" % (i + 1))
        ok, out = run_code(code)
        m = re.search(r"No module named '([\w\.]+)'", out)
        if not ok and m:
            pkg = m.group(1).split(".")[0]
            steps.append("kuruluyor: " + pkg)
            try:
                if install(pkg):
                    ok, out = run_code(code)
            except Exception as e:
                steps.append("kurulamadi: %s" % e)
        if ok:
            COUNT[0] += 1
            open(os.path.join(WORK, "gorev_%d.py" % COUNT[0]), "w").write(code)
            return reply + "\n\nCikti:\n" + (out.strip() or "(bos)")
        steps.append("hata: " + out.strip().splitlines()[-1][:120])
        msgs += [{"role": "assistant", "content": reply},
                 {"role": "user", "content": "Hata:\n%s\nDuzeltilmis TAM kodu ver." % out[-1500:]}]
    return reply + "\n\nDuzeltemedim, 5 denemede olmadi."


HIST = []


def chat(d):
    model = d.get("model") or "qwen2.5:0.5b"
    steps = []
    ENABLED[:] = _enabled_urls(d.get("mcp") or [])
    if d.get("agent"):
        return {"reply": agent(d["text"], model, steps), "steps": steps}
    HIST.append({"role": "user", "content": d["text"]})
    r = llm(HIST[-12:], model)
    HIST.append({"role": "assistant", "content": r})
    return {"reply": r}


def mcp_add(d):
    url = d.get("url", "").strip()
    c = cfg()
    if url not in c["mcp"]:
        c["mcp"].append(url)
    save_cfg(c)
    if "github.com" in url:
        z = zipfile.ZipFile(io.BytesIO(http(url.rstrip("/") + "/archive/HEAD.zip", timeout=120)))
        z.extractall(os.path.join(WORK, "mcp"))
        return {"msg": "GitHub deposu indirildi: work/mcp"}
    try:
        t = get_mcp(url).tools()
        return {"msg": "MCP baglandi, %d arac: %s" % (len(t), ", ".join(x["name"] for x in t[:8]))}
    except Exception as e:
        return {"msg": "Kaydedildi ama baglanamadi: %s (sadece Streamable HTTP desteklenir)" % e}


def gh_api(method, url, tok, body=None):
    h = {"Authorization": "token " + tok, "Accept": "application/vnd.github+json",
         "Content-Type": "application/json", "User-Agent": "ajan"}
    data = json.dumps(body).encode() if body else None
    return json.loads(urlopen(Request(url, data, h, method=method), timeout=60).read() or b"{}")


def git_push(name, msg):
    tok = cfg().get("token")
    if not tok:
        return "Once: git config token <GitHub token>"
    dest = os.path.join(PROJ, os.path.basename(name))
    o = json.load(open(os.path.join(dest, ".ajan_origin")))
    base = "https://api.github.com/repos/%s/%s/contents/" % (o["user"], o["repo"])
    n = 0
    for root, dirs, files in os.walk(dest):
        dirs[:] = [x for x in dirs if x not in (".git", "__pycache__")]
        for f in files:
            p = os.path.join(root, f)
            if f == ".ajan_origin" or os.path.getsize(p) > 1000000:
                continue
            rel = os.path.relpath(p, dest).replace(os.sep, "/")
            body = {"message": msg, "content": base64.b64encode(open(p, "rb").read()).decode()}
            try:
                body["sha"] = gh_api("GET", base + rel, tok)["sha"]
            except Exception:
                pass
            gh_api("PUT", base + rel, tok, body)
            n += 1
            if n >= 50:
                return "50 dosya gonderildi (sinir)"
    return "%d dosya gonderildi" % n


def git(cmd):
    a = shlex.split(cmd)[1:]
    if not a:
        return "git clone <link> | git pull <proje> | git push <proje> [mesaj] | git config token <token>"
    if a[0] == "clone" and len(a) > 1:
        return gh_get({"url": a[1]})["msg"]
    if a[0] in ("pull", "push") and len(a) > 1:
        dest = os.path.join(PROJ, os.path.basename(a[1]))
        if not os.path.exists(os.path.join(dest, ".ajan_origin")):
            return "Proje yok: " + a[1]
        if a[0] == "push":
            return git_push(a[1], " ".join(a[2:]) or "ajan")
        o = json.load(open(os.path.join(dest, ".ajan_origin")))
        return gh_get({"url": "https://github.com/%s/%s" % (o["user"], o["repo"])})["msg"] + " (yerel degisiklikler silindi)"
    if a[0] == "config" and len(a) > 2 and a[1] == "token":
        save_cfg(dict(cfg(), token=a[2]))
        return "Token kaydedildi"
    return "Desteklenmiyor: " + a[0]


# ---------- MCP (Streamable HTTP, saf Python) ----------
class MCP:
    def __init__(self, url):
        self.url, self.sid, self.n, self.ready, self.tl = url, None, 0, False, None

    def rpc(self, method, params=None, notify=False):
        self.n += 1
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = self.n
        h = {"Content-Type": "application/json", "User-Agent": "ajan",
             "Accept": "application/json, text/event-stream"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        resp = urlopen(Request(self.url, json.dumps(msg).encode(), h), timeout=60)
        self.sid = resp.headers.get("Mcp-Session-Id") or self.sid
        raw = resp.read().decode("utf-8", "ignore")
        if notify or not raw.strip():
            return None
        if not raw.lstrip().startswith("{"):
            for line in raw.splitlines():
                if line.startswith("data:"):
                    try:
                        if json.loads(line[5:]).get("id") == self.n:
                            raw = line[5:]
                            break
                    except Exception:
                        pass
        d = json.loads(raw)
        if "error" in d:
            raise Exception(d["error"].get("message", "mcp hata"))
        return d.get("result")

    def start(self):
        if not self.ready:
            self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "ajan", "version": "0.1"}})
            self.rpc("notifications/initialized", {}, notify=True)
            self.ready = True

    def tools(self):
        if self.tl is None:
            self.start()
            self.tl = self.rpc("tools/list", {}).get("tools", [])
        return self.tl

    def call(self, name, args):
        self.start()
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        return "\n".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text") or json.dumps(r)


MCPS, ENABLED = {}, []


def get_mcp(url):
    if url not in MCPS:
        MCPS[url] = MCP(url)
    return MCPS[url]


def _enabled_urls(names):
    return [u for u in cfg()["mcp"] if re.sub(r"^https?://", "", u)[:40] in names and "github.com" not in u]


def _mcp_tool(tool, **args):
    for u in ENABLED:
        try:
            if any(t["name"] == tool for t in get_mcp(u).tools()):
                return get_mcp(u).call(tool, args)
        except Exception as e:
            return "mcp hata: %s" % e
    return "arac bulunamadi: " + tool


def mcp_desc():
    lines = []
    for u in ENABLED:
        try:
            for t in get_mcp(u).tools()[:12]:
                props = ", ".join((t.get("inputSchema") or {}).get("properties", {}).keys())
                lines.append("mcp('%s', %s) - %s" % (t["name"], props or "", (t.get("description") or "")[:90]))
        except Exception:
            pass
    return ("\nMCP araclari (string dondurur, print ile yaz):\n" + "\n".join(lines)) if lines else ""


def _git_tool(c):
    c = c.strip()
    return git(c if c == "git" or c.startswith("git ") else "git " + c)


def _sh_tool(c):
    return term({"cmd": c})["out"]


def _gitcmd(c):
    s = c.strip() if isinstance(c, str) else " ".join(c)
    s = s.strip()
    return (s == "git" or s.startswith("git ")), s


def _sp_run(args, *a, **k):
    ok, s = _gitcmd(args)
    if ok:
        return subprocess.CompletedProcess(args, 0, git(s) + "\n", "")
    return _real_run(args, *a, **k)


def _sp_co(args, *a, **k):
    ok, s = _gitcmd(args)
    if ok:
        out = git(s) + "\n"
        return out if (k.get("text") or k.get("universal_newlines")) else out.encode()
    return _real_co(args, *a, **k)


def _sp_system(c):
    ok, s = _gitcmd(c)
    if ok:
        sys.stdout.write(git(s) + "\n")
        return 0
    return _real_system(c)


subprocess.run = _sp_run
subprocess.check_output = _sp_co
os.system = _sp_system


def term(d):
    if d["cmd"].strip() == "git" or d["cmd"].strip().startswith("git "):
        try:
            return {"out": git(d["cmd"].strip())}
        except Exception as e:
            return {"out": "git hata: %s" % e}
    p = _real_run(d["cmd"], shell=True, capture_output=True, text=True,
                       timeout=30, cwd=WORK)
    return {"out": (p.stdout + p.stderr).strip()}


ENTRIES = ("main.py", "app.py", "run.py", "bot.py", "index.html")


def entry(dest):
    for f in ENTRIES:
        if os.path.exists(os.path.join(dest, f)):
            return f
    return None


def gh_get(d):
    m = re.search(r"github\.com/([^/]+)/([^/#?]+)", d["url"].strip())
    if not m:
        return {"msg": "GitHub linki degil"}
    user, repo = m.group(1), m.group(2).replace(".git", "")
    try:
        data = http("https://github.com/%s/%s/archive/HEAD.zip" % (user, repo), timeout=180)
    except Exception as e:
        return {"msg": "Indirilemedi: %s" % e}
    tmp = os.path.join(PROJ, "_tmp")
    dest = os.path.join(PROJ, repo)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(dest, ignore_errors=True)
    zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    shutil.move(os.path.join(tmp, os.listdir(tmp)[0]), dest)
    json.dump({"user": user, "repo": repo}, open(os.path.join(dest, ".ajan_origin"), "w"))
    shutil.rmtree(tmp, ignore_errors=True)
    done = []
    req = os.path.join(dest, "requirements.txt")
    if os.path.exists(req):
        for line in open(req, errors="ignore"):
            name = re.split(r"[<>=!~\[; #]", line.strip())[0]
            if name:
                try:
                    install(name)
                    done.append(name)
                except Exception:
                    done.append(name + " (kurulamadi)")
    return {"msg": "Indirildi: %s | giris: %s | paket: %s" % (repo, entry(dest), ", ".join(done) or "yok")}


def gh_list(d):
    out = []
    for n in sorted(os.listdir(PROJ)):
        p = os.path.join(PROJ, n)
        if os.path.isdir(p):
            out.append({"name": n, "entry": entry(p)})
    return {"projects": out}


def gh_run(d):
    dest = os.path.join(PROJ, os.path.basename(d["name"]))
    e = entry(dest)
    if not e:
        return {"out": "Baslangic dosyasi yok (main.py / index.html)"}
    if e.endswith(".html"):
        return {"open": "/p/%s/%s" % (os.path.basename(dest), e)}
    src = open(os.path.join(dest, e), errors="ignore").read()
    old = os.getcwd()
    os.chdir(dest)
    sys.path.insert(0, dest)
    try:
        ok, out = run_code(src, 60)
        mm = re.search(r"No module named '([\w\.]+)'", out)
        if not ok and mm:
            install(mm.group(1).split(".")[0])
            ok, out = run_code(src, 60)
    finally:
        os.chdir(old)
    return {"out": (out.strip() or "(cikti yok, arka planda calisiyor olabilir)")}


ROUTES = {
    "/gh/get": gh_get,
    "/gh/list": gh_list,
    "/gh/run": gh_run,
    "/chat": chat,
    "/term": term,
    "/mcp/add": mcp_add,
    "/pkg/install": lambda d: {"msg": "kuruldu" if install(d["name"]) else "wheel yok"},
    "/pkg/clean": lambda d: {"msg": clean()},
    "/model/pull": lambda d: {"ok": pull(d["name"])},
    "/config": cfg_set,
    "/api/models": api_models_route,
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, data, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/p/"):
            f = os.path.realpath(os.path.join(PROJ, self.path[3:].split("?")[0]))
            if f.startswith(os.path.realpath(PROJ)) and os.path.isfile(f):
                return self.send(open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
            self.send_response(404)
            self.end_headers()
            return
        self.send(open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n) or b"{}")
        try:
            out = ROUTES[self.path](d)
        except Exception as e:
            out = {"reply": "Hata: %s" % e, "out": "Hata: %s" % e, "msg": "Hata: %s" % e}
        self.send(json.dumps(out).encode())


def serve():
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
