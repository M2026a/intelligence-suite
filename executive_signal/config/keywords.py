from __future__ import annotations

MODULE_KEYWORDS = {
    "company": {
        "entities": [
            "OpenAI", "Microsoft", "Google", "NVIDIA", "Intel", "TSMC",
            "Samsung", "Micron", "AMD", "Toyota", "Sony", "Meta", "Amazon"
        ],
        "positive": [
            "invest", "investment", "launch", "partnership", "expand", "expansion",
            "acquire", "acquisition", "announce", "agreement", "record", "growth"
        ],
        "negative": [
            "cut", "delay", "risk", "fine", "loss", "lawsuit", "probe",
            "decline", "restructuring", "shutdown"
        ],
    },
    "price": {
        "positive": [
            "raise", "inflation", "increase", "tighten", "higher", "surge",
            "shortage", "hawkish", "upside"
        ],
        "negative": [
            "lower", "ease", "decline", "fall", "cut", "slump", "deflation",
            "dovish", "downside"
        ],
        "targets": ["DRAM", "NAND", "USD/JPY", "oil", "rate", "yield", "pricing", "cost"],
    },
    "supply_chain": {
        "positive": ["capacity", "expansion", "shipment", "output", "investment", "ramp", "fab"],
        "negative": ["shortage", "bottleneck", "delay", "disruption", "constraint", "earthquake", "fire"],
        "targets": ["ASML", "Applied Materials", "resist", "gas", "wafer", "logistics", "lithography"],
    },
    "failure": {
        "positive": ["resolved", "restored", "patched", "mitigation", "containment"],
        "negative": ["outage", "breach", "incident", "vulnerability", "exploit", "malware", "leak", "ransomware"],
    },
    "earnings": {
        "positive": ["guidance raised", "record", "growth", "capex", "strong demand", "beat", "margin expansion", "決算", "業績", "増益", "上方修正", "最高益", "営業利益", "純利益", "売上高", "増収", "好調"],
        "negative": ["guidance cut", "weak demand", "inventory", "loss", "miss", "write-down", "減益", "下方修正", "赤字", "減収", "在庫調整", "需要減速", "特別損失"],
        "targets": ["決算", "通期", "四半期", "業績予想", "営業利益", "純利益"],
    },
    "geo_risk": {
        "positive": ["framework", "guideline", "cooperation", "alignment", "standard", "consultation", "協力", "合意", "枠組み", "指針", "連携", "共同声明"],
        "negative": ["restriction", "sanction", "ban", "regulation", "export control", "risk", "compliance", "tariff", "制裁", "規制", "輸出規制", "関税", "経済安全保障", "地政学", "対中", "台湾", "中東"],
        "targets": ["輸出規制", "制裁", "関税", "経済安全保障", "外為法", "AI法", "半導体規制"],
    },
    "talent": {
        "positive": ["hiring", "recruit", "training", "upskill", "fellowship", "reskilling", "採用", "育成", "研修", "人材開発", "リスキリング", "教育", "増員"],
        "negative": ["layoff", "freeze", "shortage", "attrition", "人手不足", "採用難", "退職", "削減", "凍結"],
        "targets": ["採用", "人材", "育成", "研修", "リスキリング", "人手不足"],
    },
    "product": {
        "positive": ["launch", "release", "introducing", "announces", "new", "preview", "general availability", "発表", "発売", "提供開始", "新製品", "新サービス", "量産開始", "導入", "受注開始"],
        "negative": ["delay", "issue", "recall", "rollback", "withdrawal", "延期", "不具合", "回収", "停止"],
        "targets": ["新製品", "新サービス", "発表", "発売", "量産", "提供開始"],
    },
}
