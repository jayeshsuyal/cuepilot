"""Render Dock's proposed architecture as editable SVGs and a vector PDF.

Uses the bundled document runtime; creates documentation, not application code.
"""
from pathlib import Path
import math

from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from reportlab.graphics import renderPDF, renderSVG
from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output" / "architecture"
PDF = ROOT / "output" / "pdf" / "dock-system-architecture.pdf"
W, H = 1600, 1000
C = dict(ink="#13243A", muted="#53657A", line="#B7C5D4", bg="#F7F9FC",
         white="#FFFFFF", cognee="#684BEB", hydra="#087D68", hotdata="#216BDD",
         rocket="#C55B16", rote="#B12B78", snyk="#5545A0", red="#AC3444",
         pale="#EAF0F7", green="#087D68")
PAGES = []


def color(value):
    return HexColor(C.get(value, value))


def rect(x, y, w, h, fill="white", stroke=None, radius=14, width=1):
    D.add(Rect(x, H-y-h, w, h, rx=radius, ry=radius, fillColor=color(fill),
               strokeColor=color(stroke) if stroke else None, strokeWidth=width))


def text(x, y, value, size=24, fill="ink", bold=False, anchor="start"):
    D.add(String(x, H-y-size*.80, value, fontName="Helvetica-Bold" if bold else "Helvetica",
                 fontSize=size, fillColor=color(fill), textAnchor=anchor))


def wrap(value, width, size=22, bold=False):
    font = "Helvetica-Bold" if bold else "Helvetica"
    result = []
    for part in value.split("\n"):
        line = ""
        for word in part.split():
            candidate = (line + " " + word).strip()
            if stringWidth(candidate, font, size) > width and line:
                result.append(line)
                line = word
            else:
                line = candidate
        result.append(line)
    return result


def paragraph(x, y, value, width, size=22, fill="muted", leading=29, bold=False):
    lines = wrap(value, width, size, bold)
    for i, line in enumerate(lines):
        text(x, y+i*leading, line, size, fill, bold)
    return len(lines)*leading


def box(x, y, w, h, title, body, accent="ink", tag=None, size=20):
    rect(x, y, w, h, "white", "line")
    rect(x, y, 6, h, accent, radius=3)
    top = y+22
    if tag:
        text(x+22, top, tag.upper(), 16, accent, True)
        top += 27
    title_lines = wrap(title, w-44, 28, True)
    for line in title_lines:
        text(x+22, top, line, 28, "ink", True)
        top += 32
    top += 11
    while size > 18 and top+len(wrap(body,w-44,size))*(size+7)>y+h-10:
        size-=1
    height = paragraph(x+22, top, body, w-44, size, leading=size+7)
    assert top+height <= y+h+3, (title, top+height, y+h)


def edge(points, accent="muted", dashed=False, both=False):
    for (x1,y1),(x2,y2) in zip(points,points[1:]):
        line = Line(x1,H-y1,x2,H-y2,strokeColor=color(accent),strokeWidth=2.2)
        if dashed:
            line.strokeDashArray=[7,6]
        D.add(line)
    def arrow(a,b):
        x,y=b; angle=math.atan2(y-a[1],x-a[0]); length=11; spread=.46
        coords=[x,H-y,x-length*math.cos(angle-spread),H-(y-length*math.sin(angle-spread)),
                x-length*math.cos(angle+spread),H-(y-length*math.sin(angle+spread))]
        D.add(Polygon(coords,fillColor=color(accent),strokeColor=None))
    arrow(points[-2],points[-1])
    if both:
        arrow(points[1],points[0])


def label(x,y,value,accent="muted",size=18):
    width=stringWidth(value,"Helvetica",size)+16
    rect(x-width/2,y-3,width,size+9,"bg",radius=4)
    text(x,y,value,size,accent,anchor="middle")


def header(index,title,subtitle):
    global D
    D=Drawing(W,H)
    rect(0,0,W,H,"bg",radius=0)
    rect(56,46,12,43,"hotdata",radius=3)
    text(84,45,"DOCK",40,"ink",True)
    text(1544,53,"ARCHITECTURE / 11 SEP 2026",17,"muted",anchor="end")
    text(56,118,title,43,"ink",True)
    text(56,171,subtitle,22,"muted")
    D.add(Line(56, H-940,1544,H-940,strokeColor=color("line"),strokeWidth=1))
    text(56,957,"TARGET DESIGN  |  One supplier. Two repairs. Three proof runs.",17,"muted")
    text(1544,957,f"{index:02d} / 06",17,"muted",anchor="end")


def finish(name):
    PAGES.append((name,D))


header(1,"System architecture","One operations agent learns a supplier's rules, reuses the procedure, and stops when the rule changes.")
box(56,235,300,155,"Dock intake","Batch CSV + supplier note\nPreview and first approval",tag="One-screen UI")
box(566,235,468,155,"RocketRide","Select and sequence tools.\nChoose learn, replay or stop.","rocket","Orchestration owner")
box(1244,235,300,155,"Proof screen","Row changes, receipts,\nrun trace and measurements",tag="Same UI")
edge([(356,310),(566,310)])
label(460,282,"run via backend")
edge([(1034,310),(1244,310)])
label(1139,282,"events + result")
box(566,495,468,170,"Dock adapter API","Bounded tools; no separate planner.\nGraph export, recall, replay, imports.","ink","Local laptop process")
edge([(800,390),(800,495)],"rocket",both=True)
label(800,435,"Authenticated HTTPS tunnel","rocket")
box(56,500,315,160,"Cognee","Extract candidate entities\nand relationships from notes.","cognee","Hosted processing")
box(56,735,315,160,"hotdata.dev","Fresh batch + catalog SQL.\nValidate every run and replay.","hotdata","Hosted query service")
box(1229,500,315,160,"HydraDB","Durable rule graph, sources,\nrecipe versions and outcomes.","hydra","Local OSS graph")
box(1229,735,315,160,"Rote / Modiqo","Record a successful path.\nReplay it with new parameters.","rote","Local procedure engine")
box(566,760,468,135,"Sandbox receiving API","Atomic version check + idempotent import.\nReturns a durable receipt.","green",size=21)
edge([(371,580),(566,580)],"cognee",both=True)
label(468,548,"ingest / export","cognee")
edge([(371,815),(468,815),(468,625),(566,625)],"hotdata",both=True)
label(467,698,"SQL checks","hotdata")
edge([(1034,555),(1229,555)],"hydra",both=True)
label(1130,522,"graph bridge + Cypher","hydra",16)
edge([(1034,625),(1137,625),(1137,815),(1229,815)],"rote",both=True)
label(1137,698,"record / replay","rote")
edge([(800,665),(800,760)],"green",both=True)
label(800,701,"guarded actions + receipts","green")
text(56,915,"Snyk build gate: source + dependency scans, fixes and rescans before submission.",17,"snyk")
finish("01-system-architecture")

header(2,"System flow","RocketRide orchestrates the runtime. Reuse requires fresh evidence and matching procedure preconditions.")
box(480,225,640,105,"Receive + check retry","Hash input; one effective date; look for an existing receipt.",size=20)
box(56,225,340,130,"Existing receipt","Same key returns same receipt.\nNo new write.","green",size=20)
edge([(480,287),(396,287)],"green")
label(438,253,"found","green",16)
paragraph(1244,236,"Unknown commit outcome?\nReconcile the receipt by key.\nNever assume zero writes.",300,20,leading=28)
edge([(800,330),(800,367)])
label(800,340,"new batch",size=16)
box(380,367,840,116,"Collect current evidence","Cognee processes new notes. Hydra retrieves prior rules and recipes.\nhotdata queries the new batch and an independent current catalog.","hotdata",size=21)
edge([(800,483),(800,518)])
rect(390,518,820,55,"pale",radius=10)
text(800,534,"Does the approved recipe match this batch's current rule?",24,"ink",True,"middle")
edge([(540,573),(270,573),(270,620)],"cognee")
edge([(800,573),(800,620)],"rote")
edge([(1060,573),(1330,573),(1330,620)],"red")
label(260,585,"NO RECIPE","cognee",17)
label(800,585,"MATCH","rote",17)
label(1325,585,"CHANGED / AMBIGUOUS","red",17)
box(56,620,425,160,"Learn + approve","Validate extracted facts.\nApprove the two-operation plan.\nExecute and record the candidate path.","cognee",size=21)
box(587,620,425,160,"Replay","Pass the new batch into Rote.\nReuse the approved procedure.\nSkip planning for known repairs.","rote",size=21)
box(1119,620,425,160,"Stop safely","Explain the mismatched rule.\nImport zero rows.\nRequire a new validated plan.","red",size=21)
edge([(270,780),(270,805),(535,805),(535,821)],"green")
edge([(800,780),(800,821)],"green")
rect(56,821,956,95,"white","green",10)
text(78,833,"Shared import gate",24,"green",True)
text(78,866,"Validate outputs; atomic catalog-version check + idempotency constraint.",20,"ink")
text(78,892,"Confirmed rejection: zero new rows. Unknown outcome: reconcile the receipt.",19,"muted")
rect(1119,813,425,103,"pale",radius=10)
paragraph(1138,828,"After verified success: publish the Rote recipe and link the receipt + outcome in HydraDB.",387,21,leading=26)
edge([(1012,879),(1119,879)],"green")
label(1065,850,"verified","green",16)
text(56,920,"Outside runtime: Snyk scans source and dependencies; findings feed fixes and rescans.",16,"snyk")
finish("02-system-flow")

header(3,"The RocketRide Designer view","A concrete node blueprint based on the installed staging catalog. Full Dock pipeline validation is still required.")
text(56,250,"SEPARATE TRACED OPERATIONS",17,"muted",True)
paragraph(56,286,"1  Extract and store evidence\n2  Recall rules and recipes\n3  Query the current batch",420,22,leading=31)
box(628,239,344,143,"Dock Operations","One authenticated bridge.\nSeparate traced operations.","ink","tool_http_request",size=20)
text(1115,250,"CONTINUED BY THE SAME AGENT",17,"muted",True)
paragraph(1115,286,"4  Preview proposed changes\n5  Execute or replay\n6  Verify the receiving receipt",420,22,leading=31)
box(56,493,300,150,"Batch source","External run request\non the questions lane.","hotdata","webhook",size=21)
box(590,478,420,180,"Dock agent","Owns the decision and tool sequence.\nOne LLM + one internal memory.\nSupplier knowledge lives in HydraDB.","rocket","agent_rocketride",size=21)
box(1244,493,300,150,"Run answer","Answer lane to the UI.\nRun JSON from the adapter.","green","response_answers",size=21)
edge([(356,570),(590,570)],"hotdata")
label(472,540,"questions","hotdata")
edge([(1010,570),(1244,570)],"green")
label(1127,540,"answers","green")
edge([(800,382),(800,478)],"ink",True)
box(180,772,315,129,"Agent LLM","Planner for novel decisions.","rocket","llm_openai",size=20)
box(642,772,316,129,"Run scratchpad","Required session memory.","muted","memory_internal",size=20)
box(1214,724,330,180,"Local adapters","Cognee / HydraDB / Hotdata\nRote / guarded receiving API\nApproval + version + retry guards.","hydra","Bridge - not a native node",size=20)
edge([(338,772),(338,705),(685,705),(685,658)],"rocket",True)
edge([(800,772),(800,658)],"muted",True)
edge([(972,382),(1570,382),(1570,700),(1379,700),(1379,724)],"ink",True)
label(1075,699,"Dashed = attachments / bridge request",size=16)
finish("03-rocketride-designer-blueprint")

header(4,"Memory model and run contract","Candidate facts become operational rules only after validation against the current authoritative catalog.")
box(56,237,310,150,"Source document","Content hash, source span,\nCognee extraction reference.","cognee","Provenance",size=21)
box(451,237,365,150,"Supplier + SKU","Rule scope includes product\nand effective date interval.","hydra","Identity and applicability",size=21)
box(901,237,310,150,"Catalog snapshot","Revision + fetched time.\nApplicable pack size.","hotdata","Current authority",size=21)
box(451,490,365,184,"Rule version","unitsPerCase + effective interval\nsource hash + catalog revision\nstatus: candidate / approved", "hydra","HydraDB",size=21)
edge([(451,578),(211,578),(211,387)],"cognee")
label(280,548,"evidenced by","cognee")
edge([(633,490),(633,387)],"hydra")
label(633,430,"applies to","hydra")
edge([(816,578),(1056,578),(1056,387)],"hotdata")
label(970,548,"verified against","hotdata")
box(56,754,360,151,"Recipe version","Ordered allowed operations.\nSchema + rule bindings.\nRote procedure ID and version.","rote",size=21)
box(851,754,360,151,"Run + receipt","Immutable batch hash.\nSelected recipe and result.\nMeasured timings and lineage.","rocket",size=21)
edge([(236,754),(236,709),(545,709),(545,674)],"rote")
label(359,682,"binds","rote")
edge([(851,833),(416,833)],"rocket")
label(634,803,"executed","rocket")
rect(1252,237,292,668,"pale",radius=14)
text(1274,261,"RunContract v1",25,"ink",True)
items=[("Identity","runId / supplierId","batchId / batchSha256"),
       ("Inputs","schemaFingerprint","effectiveDate"),
       ("Decision","catalogSnapshotId","recipeId / version"),
       ("Proof","diffs[] / checks[]","receipt / idempotencyKey"),
       ("Telemetry","elapsedMs","instrumented calls","opaque usage: unavailable")]
yy=321
for group,*fields in items:
    text(1274,yy,group.upper(),16,"muted",True);yy+=29
    for field in fields:
        text(1274,yy,field,19,"ink");yy+=27
    yy+=19
finish("04-memory-and-contract")

header(5,"Six-hour build map","Two people, shared contracts, one thin end-to-end path before polish.")
X, CW=300, 1200
for hour in range(7):
    x=X+hour*CW/6
    text(x,234,f"{hour}:00",20,"muted",anchor="middle")
    D.add(Line(x,H-270,x,H-645,strokeColor=color("line"),strokeWidth=1))
text(56,315,"YOU + CODEX",24,"ink",True)
paragraph(56,356,"Memory, evidence\nand batch validation",212,22)
text(56,504,"TEAMMATE + CODEX",23,"ink",True)
paragraph(56,545,"Execution, receipts\nand product screen",212,22)
segments=[(0,.5,"Agree\nAPI +\ncheck"),(.5,2,"Cognee -> Hydra\nHotdata checks"),(2,3,"Learn +\nfirst import"),(3,4,"Replay\nintegration"),(4,5,"Changed-rule\nproof"),(5,6,"Snyk + demo\n+ buffer")]
for start,end,title in segments:
    x=X+start*CW/6+4;ww=(end-start)*CW/6-8
    rect(x,290,ww,140,"white","line",9)
    paragraph(x+12,315,title,ww-24,18 if ww<160 else 21,"ink",leading=27,bold=True)
segments2=[(0,.5,"Agree\nAPI +\ncheck"),(.5,2,"Receiving API\nRocketRide path"),(2,3,"Approve +\nrecord run"),(3,4,"Rote replay\nnew batch"),(4,5,"Guards +\nretry test"),(5,6,"Snyk + demo\n+ buffer")]
for start,end,title in segments2:
    x=X+start*CW/6+4;ww=(end-start)*CW/6-8
    rect(x,478,ww,140,"white","line",9)
    paragraph(x+12,503,title,ww-24,18 if ww<160 else 21,"ink",leading=27,bold=True)
text(56,697,"THE THREE-MINUTE PROOF",20,"muted",True)
box(56,742,463,164,"01  Learn","10 cases x 12 = 120 units.\nShow source evidence + first receipt.","cognee",size=23)
box(568,742,463,164,"02  Replay","New batch: 7 cases x 12 = 84 units.\nShow reused procedure + actual timing.","rote",size=23)
box(1080,742,464,164,"03  Stop","Pack size changes from 12 to 6.\nShow mismatch + zero imported rows.","red",size=23)
finish("05-six-hour-build-map")

header(6,"Build gates and proof of correctness","The target architecture is ready to implement. Individual setup checks are not a working five-tool application.")
rows=[
    ("Cognee", "Cloud funded; local bundled demo passed", "Fresh hosted ingest + extracted graph export", "cognee"),
    ("HydraDB", "Local graph write and traversal passed", "Store and query Cognee-derived rules", "hydra"),
    ("hotdata.dev", "CLI installed; browser sign-in completed", "Clear account activation; run real batch SQL", "hotdata"),
    ("RocketRide", "Staging auth + echo validation passed", "Compute credits; HTTP bridge; pipeline smoke test", "rocket"),
    ("Rote / Modiqo", "Login + official Hello warm-up passed", "Record / replay the actual guarded import path", "rote"),
    ("Snyk", "Source scan completed: 0 findings", "Six dependency advisories remain open", "snyk"),
]
text(78,236,"LAYER",17,"muted",True)
text(380,236,"VERIFIED SETUP",17,"muted",True)
text(962,236,"NEXT ACCEPTANCE GATE",17,"muted",True)
for i,(name,known,gate,accent) in enumerate(rows):
    yy=278+i*88
    rect(56,yy,1488,74,"white",radius=10)
    rect(56,yy,6,74,accent,radius=3)
    text(78,yy+25,name,25,accent,True)
    text(380,yy+27,known,21,"ink")
    text(962,yy+27,gate,21,"ink")
rect(56,834,1488,82,"pale",radius=12)
text(80,850,"Execution invariants",23,"ink",True)
text(80,885,"Fresh rule checks  /  Immutable inputs  /  Two allowed repairs  /  Atomic imports  /  Measured proof  /  No hidden findings",21,"muted")
finish("06-readiness-and-acceptance")


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    PDF.parent.mkdir(parents=True,exist_ok=True)
    canvas=Canvas(str(PDF),pagesize=(W,H))
    canvas.setTitle("Dock - System Architecture and Build Blueprint")
    canvas.setAuthor("Dock hackathon team")
    for name,drawing in PAGES:
        renderSVG.drawToFile(drawing,str(OUT/(name+".svg")))
        renderPDF.draw(drawing,canvas,0,0)
        canvas.showPage()
    canvas.save()
    print(f"Created {len(PAGES)} SVG diagrams and {PDF}")


if __name__=="__main__":
    main()
