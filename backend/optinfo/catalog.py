"""Public topic taxonomy and evidence-backed company directory.

The thesis itself and any server credentials are never part of this package.
"""
from __future__ import annotations

import json
from pathlib import Path

CONTENT_DIR = Path(__file__).resolve().parents[2] / "content"
SECTIONS = [
    {"id": "research", "label": "科研前沿", "description": "光学、超快动力学与半导体研究"},
    {"id": "industry", "label": "产业观察", "description": "企业业务、产品、制造与工业应用"},
    {"id": "world", "label": "重大事件", "description": "政策、市场与具有广泛影响的公共事件"},
]

def topic(id, label, section, kind, *keywords):
    return dict(id=id, label=label, section=section, kind=kind, keywords=list(keywords))

TOPICS = [
    topic("xfel", "软 X 射线与自由电子激光", "research", "研究方向", "xfel", "free electron laser", "free-electron laser", "soft x-ray"),
    topic("radiation-damage", "EUV / X 射线辐照损伤", "research", "研究方向", "radiation damage", "damage threshold", "x-ray irradiation", "euv", "grazing incidence"),
    topic("ultrafast", "超快非平衡与电子晶格动力学", "research", "研究方向", "ultrafast", "nonequilibrium", "non-equilibrium", "electron-phonon", "nonthermal melting", "electron-lattice"),
    topic("pump-probe", "泵浦探测与时间分辨成像", "research", "研究方向", "pump-probe", "pump probe", "time-resolved", "single-shot", "phase-sensitive", "reflectivity"),
    topic("multiscale", "MC / nTTM / MD 多尺度模拟", "research", "研究方向", "two-temperature", "molecular dynamics", "monte carlo", "xtant", "xcascade", "nanodiff", "electron cascade"),
    topic("semiconductor-materials", "Si / SiC / 金刚石与薄膜界面", "research", "研究方向", "silicon carbide", "diamond", "silicon", "thin film", "interface", "carrier dynamics"),
    topic("photonics", "集成光子与光电器件", "research", "研究方向", "photonics", "photonic", "optoelectronic", "waveguide", "metasurface"),
    topic("quantum-optics", "量子光学", "research", "研究方向", "quantum optics", "entanglement", "single photon"),
    topic("journal-nphoton", "Nature Photonics", "research", "期刊", "nature photonics"),
    topic("journal-lsa", "Light: Science & Applications", "research", "期刊", "light: science", "light science"),
    topic("journal-optica", "Optica", "research", "期刊", "optica"),
    topic("journal-oe", "Optics Express", "research", "期刊", "optics express"),
    topic("journal-prb", "Physical Review B", "research", "期刊", "physical review b"),
    topic("journal-prapplied", "Physical Review Applied", "research", "期刊", "physical review applied"),
    topic("journal-applied", "Applied Physics", "research", "期刊", "applied physics"),
    topic("lithography", "EUV 光学与半导体光刻", "industry", "工业应用", "euv", "lithography", "光刻", "asml", "zeiss"),
    topic("industrial-lasers", "激光与精密加工", "industry", "工业应用", "laser", "激光", "coherent", "trumpf", "ipg"),
    topic("chip-manufacturing", "芯片制造与半导体设备", "industry", "公司业务", "semiconductor", "foundry", "wafer", "芯片", "半导体", "晶圆", "tsmc", "smic", "applied materials"),
    topic("detectors", "成像、探测与光学测量", "industry", "公司业务", "detector", "imaging", "sensor", "hamamatsu", "探测", "成像"),
    topic("corporate-strategy", "企业扩产、并购与业务调整", "industry", "公司业务", "acquisition", "investment", "capacity", "restructuring", "并购", "扩产", "裁员"),
    topic("policy", "国家政策与科技监管", "world", "重大新闻", "policy", "regulation", "export control", "政策", "监管", "出口管制"),
    topic("markets", "金融市场与宏观经济", "world", "重大新闻", "interest rate", "inflation", "market", "stock", "利率", "股市", "经济"),
    topic("global-events", "国际重大事件", "world", "重大新闻", "emergency", "war", "disaster", "global", "重大", "战争"),
]
DEFAULT_TOPIC_IDS = [t["id"] for t in TOPICS if t["id"] not in {"quantum-optics", "photonics"}]
JOURNALS = [
    {"id": "nphoton", "name": "Nature Photonics", "issn": "1749-4893"},
    {"id": "lsa", "name": "Light: Science & Applications", "issn": "2047-7538"},
    {"id": "optica", "name": "Optica", "issn": "2334-2536"},
    {"id": "oe", "name": "Optics Express", "issn": "1094-4087"},
    {"id": "prb", "name": "Physical Review B", "issn": "2469-9950"},
    {"id": "prapplied", "name": "Physical Review Applied", "issn": "2331-7019"},
    {"id": "apl", "name": "Applied Physics Letters", "issn": "0003-6951"},
    {"id": "arxiv", "name": "arXiv（预印本）"},
]
COMPANIES = json.loads((CONTENT_DIR / "companies.json").read_text(encoding="utf-8")) if (CONTENT_DIR / "companies.json").exists() else []
for company in COMPANIES:
    company.setdefault("compensation", {"masters": None, "phd": None, "currency": "CNY", "basis": "暂无可靠公开数据；学历本身不能确定薪酬，需区分地区、岗位、年份及总包口径。", "as_of": "", "sources": []})
    company.setdefault("bookmarked", False)

def match_companies(text: str) -> list[str]:
    lowered = text.casefold()
    return [c["id"] for c in COMPANIES if any(alias.casefold() in lowered for alias in [c["name"], c["name_en"], *c.get("aliases", [])] if len(alias) >= 3)]
