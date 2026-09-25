# laya-jev-GraphRAG

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![PyTorch](https://img.shields.io/badge/PyTorch-CUDA-EE4C2C.svg)
![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)
![Databases](https://img.shields.io/badge/DB-Neo4j%20%7C%20Memgraph%20%7C%20AGE%20%7C%20Kùzu-018bff.svg)
![Backend](https://img.shields.io/badge/AI%20Backend-Laya%20%7C%20Jev%20%7C%20Ablation-blueviolet.svg)

**English:** [README.md](README.md)

**laya-jev-GraphRAG**, **graf veritabanından bağımsız bir Agentic GraphRAG framework'üdür**: mevcut graf veritabanınızın üzerine yerleştirip onu tamamen agentic hale getiren, üretime hazır bir zekâ katmanı. Graf veritabanınızın yerini almaz, ona bir beyin kazandırır.

GraphRAG mantığını tek bir veritabanına sabitlemek yerine bu framework, **yapay zekâ karar katmanını depolama katmanından tamamen ayırır**. Aynı **4 fazlı pipeline** (Ingestion → Pre-Retrieval → Traversal → Post-Retrieval) Neo4j, Memgraph, Apache AGE ve Kùzu üzerinde birebir aynı şekilde çalışır; aralarında tek bir ortam değişkeniyle geçiş yapılır.

Pipeline içindeki her karar (semantik parçalamadan niyet yönlendirmesine, özel A\* traversal'dan halüsinasyon kapısına kadar) yavaş üretken LLM çağrıları yerine üç deterministik matematiksel primitif kullanan, değiştirilebilir **System One modelleri** (yerel Laya / bulut Jev) tarafından verilir:

| Primitif | Ne Yapar | Nerede Kullanılır |
|----------|----------|-------------------|
| **`Score`** | Kenarları ve ilişkileri `[0, 1]` aralığında değerlendirir | Ingestion sırasında kenar doğrulama, A\* traversal heuristiği, bağlam yeniden sıralama |
| **`Noul`**  | İkili yargı `P(evet)` `[0, 1]` | Semantik parçalama, varlık ayrıştırma, erken sonlandırma, halüsinasyon kapısı, kaynak doğrulama |
| **`Choice`**| Kategorik seçim | Niyet yönlendirme, ontoloji hizalama, çelişki çözümü |

Hem **yapay zekâ karar modelini** hem de **graf veritabanını** birbirinden bağımsız olarak, her biri için tek bir ortam değişkeniyle değiştirebilirsiniz.

---

## 🛑 Geleneksel GraphRAG Neden Başarısız Olur

Geleneksel GraphRAG'in üç temel sorunu vardır:

1. **Her adımda LLM:** 4 derinlikte 5 kenarı değerlendirmek = 20 ardışık LLM çağrısı = 30–90 saniye gecikme ve sık sık bağlam penceresi taşması.
2. **Kenar doğrulaması yok:** Ingestion sırasında çıkarılan halüsinasyon ilişkilere sonsuza dek körü körüne güvenilir. Kötü veri her adımda katlanarak büyür.
3. **Veritabanına bağımlılık:** Geleneksel uygulamalar graf mantığını tek bir veritabanına sabitler. Neo4j yerine Memgraph ya da AGE kullanmak tüm pipeline'ın yeniden yazılmasını gerektirir.

## ⚡ Çözüm: Uçtan Uca System One Değerlendirmesi

Bu framework üç sorunu da çözer: LLM yönlendirmesini System One modelleriyle değiştirir, ingestion sırasında her kenarı aktif olarak doğrular ve veritabanını tek bir ortak arayüzün arkasına tamamen soyutlar:

| Faz | Laya/Jev Neyi Değerlendirir |
|-----|-----------------------------|
| **Ingestion** | Halüsinasyonları temizlemek için kenar geçerliliğini puanlar, graf şişmesini önlemek için varlıkları ayrıştırır ve katı bir şemaya uymak için ilişkileri hizalar |
| **Pre-Retrieval** | Gereksiz hesaplamayı atlamak için sorgu niyetini yönlendirir ve aramanın en uygun başlangıç noktasından başlaması için seed düğümleri doğrular |
| **Traversal** | Laya/Jev, akıllı semantik yönlendirme için özel A\* araması sırasında kenarları dinamik olarak puanlar; bağlam şişmesini önlemek ve hesaplamadan tasarruf etmek için erken sonlandırmayı denetler |
| **Post-Retrieval** | Token yoğunluğunu en üst düzeye çıkarmak için bağlamı yeniden sıralar, doğruluk için çelişen kaynakları çözer, halüsinasyonları engeller ve LLM sentezinden önce kaynakları sıkı biçimde doğrular |

Üretken LLM (Llama-3.1-8B) yalnızca **bir kez**, en sonda, zaten doğrulanmış alt grafı nihai cevaba dönüştürmek için çalışır.

---

## 🧠 Mimari Felsefe: Üç Ayrı Katman

Çoğu GraphRAG sistemi depolama, akıl yürütme ve üretimi değiştirilmesi zor tek bir yapıya sıkıca bağlar. Bu framework onları **birbirinden tamamen bağımsız üç katmana** ayırır:

```
┌───────────────────────────────────────────────────────┐
│  KATMAN 1 — DEPOLAMA  (Graf Veritabanınız)            │
│  Neo4j · Memgraph · Apache AGE · Kùzu                 │
│  Görevi: graf yapısı, PageRank, Cypher sorguları       │
└───────────────────────────┬───────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────┐
│  KATMAN 2 — KARAR  (Laya / Jev)   ← İşlemci           │
│  Görevi: her yönlendirme, puanlama ve kapı kararı      │
│  19 pipeline adımının tamamında Score · Noul · Choice  │
└───────────────────────────┬───────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────┐
│  KATMAN 3 — ÜRETİM  (Llama-3.1-8B 4-bit)              │
│  Görevi: varlık çıkarma (ingestion) +                  │
│          nihai cevap sentezi (post-retrieval)          │
│  Doküman yaşam döngüsü başına tam İKİ kez çalışır      │
└───────────────────────────────────────────────────────┘
```

Laya/Jev, **bilgi grafınızın işlemcisi** gibi çalışır: tek bir token bile üretmeden depolama ile üretim arasında veri yönlendiren yüksek hızlı karar motoru.

### 🔄 İki Eksenli Değiştirilebilirlik

Temel mimari karar şudur: **iki eksen birbirinden bağımsızdır**.

| Eksen | Seçenekler | Nasıl Değiştirilir |
|-------|------------|--------------------|
| **Yapay Zekâ Karar Modeli** | Laya (yerel) ↔ Jev (bulut API) ↔ Ablation (ikisi birden) | `DECISION_MODEL_BACKEND=laya\|jev\|ablation` |
| **Graf Veritabanı** | Neo4j ↔ Memgraph ↔ Apache AGE ↔ Kùzu | `GRAPH_DB_TYPE=neo4j\|memgraph\|postgres_age\|kuzu` |

Üretimde **Jev + Neo4j**, Docker olmadan yerel geliştirmede **Laya + Kùzu**, eğitim verisi üretmek için **Ablation + Memgraph** kullanabilirsiniz; hepsi aynı kod tabanından, kodda hiçbir değişiklik yapmadan.

### 🔁 RLCD Döngüsü: Buluttan Tamamen Yerele

Framework kendi kendini geliştiren bir döngü etrafında tasarlanmıştır:

```
1. BAŞLA    →  Jev ile yayına al (bulut API, zero-shot doğru, anında kurulum)
2. TOPLA    →  Ablation modu her Laya ve Jev kararını yan yana JSONL'e kaydeder
3. EĞİT     →  Bu JSONL kayıtlarını Laya'yı fine-tune etmek için RLCD sentetik eğitim verisi olarak kullan
4. GEÇ      →  %100 yerel ve sıfır API maliyeti için DECISION_MODEL_BACKEND=laya yap
```

Varılan nokta: API bağımlılığı olmayan ve hiçbir verinin ağınızın dışına çıkmadığı, kendi donanımınızda çalışan **tamamen yerel, üst seviye kalitede bir GraphRAG motoru**.

---

## 🔌 Backend Yapılandırması

```bash
# Yapay Zekâ Karar Modeli (.env)
DECISION_MODEL_BACKEND=laya      # Yerel (CUDA veya CPU), ücretsiz, %100 gizli
DECISION_MODEL_BACKEND=jev       # TypeSafe bulut API, zero-shot hazır, ~50ms/çağrı
DECISION_MODEL_BACKEND=ablation  # İKİSİNİ de çalıştır, RLCD fine-tuning için yan yana kaydet

# Graf Veritabanı (.env)
GRAPH_DB_TYPE=neo4j          # Üretim: index-free adjacency, yerel GDS
GRAPH_DB_TYPE=memgraph       # Bellek içi Bolt: aynı Cypher, düşük gecikmeli analitik
GRAPH_DB_TYPE=postgres_age   # PostgreSQL + Apache AGE: birleşik SQL/graf yapısı
GRAPH_DB_TYPE=kuzu           # Gömülü, yerel: Docker yok, geliştirme için sıfır kurulum
```

| Özellik | Laya (Yerel) | Jev (Bulut) |
|---------|--------------|-------------|
| Model | `convaiinnovations/laya` (multilingual 322M, varsayılan) | `jev-1.13` (TypeSafe) |
| Gecikme | ~33 ms (RTX 5060 FP16) | ~50 ms (API gidiş-dönüş) |
| Maliyet | Ücretsiz | ~$0.042 / M token |
| Gizlilik | %100 yerel | API |
| Toplu işlem | GPU ile toplu | Gerçek paralel (1 API çağrısı) |

Dört graf veritabanı backend'inin tamamı aynı `BaseGraphClient` arayüzünü uygular. Veritabanı değiştirirken **kodda hiçbir değişiklik** gerekmez.

---

## 🗺️ 19 Fonksiyonlu Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  FAZ 1 — Ingestion (Çevrimdışı, doküman başına bir kez) │
│                                                         │
│  1. Semantik Parçalama     → Noul   (sınır tespiti)     │
│  2. Varlık Çıkarma         → LLM    (Llama-3.1-8B)      │
│  3. Varlık Ayrıştırma      → Noul   (kopyaları birleştir)│
│  4. Kenar Doğrulama        → Score  (halüsinasyonu buda) │
│  5. Ontoloji Hizalama      → Choice (şemaya oturt)       │
│  6. Topluluk Tespiti       → Leiden / NetworkX           │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  FAZ 2 — Pre-Traversal (Her sorguda)                    │
│                                                         │
│  7. Niyet Yönlendirme      → Choice (local/multi/global) │
│  8. Dense Seed Getirme     → Embedding cosine           │
│  9. Seed Doğrulama         → Score  (kötü seed'leri ele) │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  FAZ 3 — A* Traversal (Her sorguda)                     │
│                                                         │
│  10. Komşuluk Getirme      → Bolt / Cypher              │
│  11. Kenar Puanlama        → Score  (semantik heuristik) │
│  12. Yapısal Çıpa          → PageRank (merkezilik)       │
│  13. Yol Budama            → Beam kesimi                │
│  14. Erken Sonlandırma     → Noul   (bağlam yeterli mi?) │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  FAZ 4 — Post-Traversal (Her sorguda)                   │
│                                                         │
│  15. Bağlam Yeniden Sıralama → Score  (düşük alakayı at) │
│  16. Çelişki Çözümü        → Choice (güvenilir kaynak)   │
│  17. Halüsinasyon Kapısı   → Noul   (güvensizse çekimser)│
│  18. Cevap Sentezi         → LLM    (Llama-3.1-8B 4-bit) │
│  19. Kaynak Doğrulama      → Noul   (dayanaksızı işaretle)│
└─────────────────────────────────────────────────────────┘
```

> **📚 19 fonksiyonun tam dökümünü mü görmek istiyorsunuz?**
> 4 fazın tamamındaki her primitif ve yönlendirme kararının ayrıntılı açıklaması için [Mimari Derinlemesine (ARCHITECTURE.md)](ARCHITECTURE.md) belgesine bakın.

> **💡 Bu statik bir bilgi deposu mu, yoksa agentic bir bellek mi? (Ve sıra dışı kullanım senaryoları)**
> Ayrıştırılmış zekâ katmanının bu sistemi hem yüksek doğruluklu bir sorgu motoru (dolandırıcılık, biyomedikal) hem de otonom ajanlar için kendi kendini düzenleyen bir bellek deposu olarak nasıl çalıştırdığını görmek için [Kullanım Senaryoları (USE_CASES.md)](USE_CASES.md) belgesine bakın.

---

## 🚀 Başlarken

### Kurulum

```bash
git clone https://github.com/bodepudimuneendra-netizen/laya-jev-GraphRAG.git
cd laya-jev-GraphRAG/graphrag_neo4j_laya

# İsteğe bağlı: CUDA 12.4'lü PyTorch (yalnız CPU olan makinelerde atlayın, Laya CPU'da da çalışır)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# Kalan tüm bağımlılıklar (`laya` ve `kuzu` dahil)
pip install -r requirements.txt
```

### Hızlı Başlangıç: Laya + Kùzu, Docker gerekmez

Pipeline'ın tamamını çalışırken görmenin en hızlı yolu paketle gelen örnektir. Karar modeli olarak **Laya** (yerel, API anahtarı gerekmez), graf veritabanı olarak **Kùzu** (gömülü, Python sürecinin içinde çalışır) kullanır. CPU'da çalışır. GPU hızlandırır ama gerekli değildir.

```bash
cd graphrag_neo4j_laya
python examples/laya_kuzu_quickstart.py
```

İlk çalıştırmada Laya multilingual checkpoint'i (~1,3 GB) ve multilingual embedding modeli indirilir. Sonrasında tam çalışma (ingestion ve üç sorgu) CPU'da yaklaşık bir dakika sürer.

Script'in `examples/data/science_history.json` ile yaptıkları:

| Adım | Laya primitifi | Ne olur |
|------|----------------|---------|
| 1. Varlıkları yükle | — | Açıklamaları ve embedding'leriyle 14 düğüm Kùzu'ya yazılır |
| 2. Kenar doğrulama | `Noul` | Çıkarılan her üçlü, geldiği kaynak metne karşı kontrol edilir; desteklenmeyen üçlüler atılır |
| 3. Ontoloji hizalama | `Choice` | Serbest metin ilişkiler (`"first detected"`) veri setinin şemasına (`DISCOVERED`) eşlenir |
| 4. Yapı | — | PageRank ve bağlı bileşenler Kùzu'ya geri yazılır |
| 5. Sorgu | `Choice` · `Score` · `Noul` | Niyet yönlendirme, seed doğrulama, A\*/BFS traversal, yeniden sıralama, halüsinasyon kapısı, kaynak kontrolü |

CPU'da alınan çıktı (varsayılan multilingual checkpoint):

```text
[2/4] Edge verification against source text (Laya Noul, keep >= 0.5)
  keep  P=0.996  Isaac Newton -[was born in]-> Woolsthorpe
  keep  P=0.974  LIGO -[first detected]-> Gravitational Waves
  ...
  PRUNE P=0.000  Isaac Newton -[baked]-> Banana Bread        ← halüsinasyon, kaldırıldı
  PRUNE P=0.006  LIGO -[was born in]-> Ulm                   ← halüsinasyon, kaldırıldı

[3/4] Ontology alignment (Laya Choice)
                     'wrote' → AUTHORED
          'was president of' → LEADS
            'first detected' → DISCOVERED

Q: Where was Isaac Newton born?
- Woolsthorpe: Isaac Newton BORN_IN Woolsthorpe. Hamlet in Lincolnshire, England, where Isaac Newton was born.
- Isaac Newton: English physicist and mathematician, author of the laws of motion and universal gravitation.

Q: How is Newton's work connected to Einstein's theory of gravity?
I don't have enough verified information in my knowledge graph to confidently answer this question. ...

Q: Yerçekimi dalgalarını ilk kim tespit etti?
[⚠️ UNVERIFIED] - Gravitational Waves: LIGO DISCOVERED Gravitational Waves. Ripples in spacetime predicted by general relativity.
...
```

Bu çalışmadaki her sonuç, pipeline'daki bir güvenlik kontrolünün işini yapmasından gelir:
- **S1 ("Newton nerede doğdu?"):** `local` olarak yönlendirildi, doğrulanmış `BORN_IN` kenarından cevaplandı ve kaynak kontrolünü geçti.
- **S2 (Newton–Einstein bağlantısı):** `multi_hop` olarak, yani çok adımlı aramaya doğru şekilde yönlendirildi. Halüsinasyon kapısı getirilen bağlamı yetersiz buldu ve pipeline tahmin yürütmek yerine cevap vermedi.
- **S3** (Türkçe soru, İngilizce veri üzerinde): arama `LIGO DISCOVERED Gravitational Waves` olgusunu buldu. Kaynak kontrolü 0,87 ile 0,90 eşiğinin hemen altında kaldığı için cevap `[⚠️ UNVERIFIED]` olarak işaretlendi.

#### Hızlı başlangıç seçenekleri

```bash
# Kendi sorularınız (tekrarlanabilir)
python examples/laya_kuzu_quickstart.py -q "Where was Albert Einstein born?" -q "Who wrote Principia Mathematica?"

# Her fazı logla: niyet, seed'ler, kapı ve kaynak kontrolü skorları
python examples/laya_kuzu_quickstart.py -v

# Kendi veri setiniz, daha derin A* araması, daha sıkı kenar doğrulama
python examples/laya_kuzu_quickstart.py --data my_graph.json --max-depth 4 --support-threshold 0.8

# Extractive cevap yerine gerçek LLM sentezi (CUDA + bitsandbytes gerektirir)
python examples/laya_kuzu_quickstart.py --llm llama
```

| Parametre | Varsayılan | Anlamı |
|-----------|------------|--------|
| `--data` | `examples/data/science_history.json` | Yüklenecek veri seti |
| `-q / --query` | veri setindeki `queries` | Çalıştırılacak soru (tekrarlanabilir) |
| `-d / --max-depth` | `3` | Maksimum A\* adım derinliği |
| `--support-threshold` | `0.5` | Bir üçlünün tutulması için gereken minimum Laya `P(kaynak kenarı destekliyor)` değeri |
| `--llm` | `extractive` | `extractive` doğrulanmış bağlamın kendisini döndürür (GPU gerekmez); `llama` Llama-3.1-8B NF4 kullanır |
| `-v / --verbose` | kapalı | Her pipeline fazının log satırını yazdırır |

Script `DECISION_MODEL_BACKEND=laya` ve `GRAPH_DB_TYPE=kuzu` değerlerini sabitler ve veritabanını her çalıştırmada yeniden kurar. Geri kalan her şey (Laya checkpoint'i, eşikler) yine `.env` dosyasından gelir.

#### Kendi verinizi kullanın

Veri seti tek bir JSON dosyasıdır. `triples`, bir LLM çıkarma adımının üreteceği şeydir: serbest metin bir ilişki ve çıkarıldığı doküman parçasının kimliği.

```json
{
  "schema":    { "BORN_IN": "was born in a place", "DISCOVERED": "discovered, detected or first observed something" },
  "documents": { "doc1": "Marie Curie was born in Warsaw. She discovered polonium and radium." },
  "entities":  { "Marie Curie": "Physicist and chemist, pioneer of radioactivity research.",
                 "Warsaw": "Capital city of Poland." },
  "triples":   [ ["Marie Curie", "was born in", "Warsaw", "doc1"] ],
  "queries":   [ "Where was Marie Curie born?" ]
}
```

`schema` isteğe bağlıdır; verilmezse `graphrag/ingestion/ontology_aligner.py` içindeki yerleşik 17 tipli ontoloji kullanılır. Alanınıza göre yazılmış küçük bir şema çok daha iyi hizalanır.

### Kullanım örnekleri (Python)

#### 1. Laya primitiflerini doğrudan kullanın

```python
from graphrag.models.laya import get_laya

laya = get_laya()   # convaiinnovations/laya (multilingual) bir kez yüklenir, sonra bellekte kalır

# Score: [0, 1] aralığında sıralı alaka
laya.score(
    "Fact: LIGO DISCOVERED Gravitational Waves.",
    "How relevant is this fact to answering: 'Who first detected gravitational waves?'",
)   # → 0.65

# Noul: ikili P(evet)
laya.noul(
    "Source text: Isaac Newton was born in Woolsthorpe.\n\n"
    "Extracted relationship: Isaac Newton -[baked]-> Banana Bread",
    "Does the source text explicitly support this extracted relationship?",
)   # → 0.03

# Choice: tek bir seçenek anahtarı döndürür; tüm seçenekler tek bir ileri geçişte puanlanır
laya.choice(
    "User question: How is Newton's work connected to Einstein's theory of gravity?",
    "Which graph retrieval strategy should be used to answer this question?",
    {
        "local":     "The question asks about one specific fact of one entity.",
        "multi_hop": "The question asks how two or more entities are connected, requiring a chain of facts.",
        "global":    "The question asks for a broad summary or overview of a whole topic.",
    },
)   # → "multi_hop"

# Aynı durum hakkında birden çok soru, tek bir ileri geçişte cevaplanır
laya.ask_batch(
    "Customer: my invoice was charged twice this month, please refund one.",
    {
        "is_billing": {"type": "noul",   "instruction": "Is this a billing issue?"},
        "urgency":    {"type": "score",  "instruction": "How urgent is this request?"},
        "team":       {"type": "choice", "instruction": "Route to which team?",
                       "options": {"billing": "Billing team", "tech": "Technical support"}},
    },
)   # → {"is_billing": DecisionResult(score=0.81, ...), "team": DecisionResult(selected="billing", ...), ...}
```

Her `*_detailed` metodu (`score_detailed`, `noul_detailed`, `choice_detailed`) olasılıkları, güven değerini ve gecikmeyi taşıyan bir `DecisionResult` döndürür.

Not: Multilingual checkpoint sayesinde bağlam ve talimatlar Türkçe de yazılabilir.

#### 2. Kendi kodunuzdan bir Kùzu grafı kurun ve sorgulayın

```python
import os
os.environ["GRAPH_DB_TYPE"] = "kuzu"            # veya .env içinde ayarlayın
os.environ["DECISION_MODEL_BACKEND"] = "laya"

from sentence_transformers import SentenceTransformer

from config.settings import settings
from graphrag.graph.kuzu_client import KuzuClient
from graphrag.ingestion.edge_verifier import EdgeVerifier
from graphrag.ingestion.ontology_aligner import OntologyAligner
from graphrag.pipeline import GraphRAGPipeline

source = "Marie Curie was born in Warsaw. She discovered polonium and radium."
entities = {
    "Marie Curie": "Physicist and chemist, pioneer of research on radioactivity.",
    "Warsaw": "Capital city of Poland, where Marie Curie was born.",
    "Paris": "Capital city of France.",
    "Polonium": "Radioactive chemical element discovered by Marie Curie in 1898.",
    "Radium": "Radioactive chemical element discovered by the Curies in 1898.",
}
triples = [
    ("Marie Curie", "was born in", "Warsaw"),
    ("Marie Curie", "discovered", "Polonium"),
    ("Marie Curie", "discovered", "Radium"),
    ("Marie Curie", "was born in", "Paris"),     # halüsinasyon → reddedilir (P≈0.01)
]

db = KuzuClient()                               # KUZU_DB_PATH, varsayılan ./kuzu_db
db.create_schema()
embedder = SentenceTransformer(settings.embed_model_id)
for name, description in entities.items():
    db.upsert_node(name, properties={"description": description})
    db.set_embedding(name, embedder.encode(f"{name}: {description}", normalize_embeddings=True).tolist())

verifier, aligner = EdgeVerifier(db), OntologyAligner()
for src, raw_rel, tgt in triples:
    if verifier.verify_against_source(src, raw_rel, tgt, source) >= 0.5:        # Noul
        db.upsert_edge(src, tgt, aligner.align(raw_rel, source=src, target=tgt))  # Choice
db.run_pagerank()


class EchoLLM:
    """Yer tutucu sentezleyici: doğrulanmış bağlam satırlarını döndürür (GPU gerekmez)."""
    def generate(self, prompt: str, **_) -> str:
        return "\n".join(l for l in prompt.splitlines() if l.startswith("- "))


pipeline = GraphRAGPipeline(graph_client=db, llm=EchoLLM())   # llm=None → Llama-3.1-8B
print(pipeline.query("Where was Marie Curie born?"))
```

> **Kùzu notu:** `KuzuClient` düğüm embedding'lerini süreç belleğinde tutar. Grafı **aynı süreç içinde** kurup sorgulayın (yukarıdaki örnekte ve hızlı başlangıçta olduğu gibi). Başka bir süreçte kurulmuş Kùzu veritabanına karşı `python -m graphrag.pipeline` çalıştırırsanız seed seçimi hiçbir giriş noktası bulamaz. Süreçten bağımsız yaşayan bir graf için Neo4j, Memgraph veya AGE kullanın.

#### 3. Tam CLI pipeline'ını çalıştırın (Neo4j / Memgraph / AGE + Llama)

```bash
docker compose up -d neo4j          # veya: memgraph | apache-age (bkz. docker-compose.yml)
cp .env.example .env                # GRAPH_DB_TYPE=neo4j, HUGGINGFACE_TOKEN=... ayarlayın
python -m graphrag.pipeline --query "What caused the 2008 financial crisis?" --max-depth 4
```

```python
from graphrag.pipeline import GraphRAGPipeline

pipeline = GraphRAGPipeline()       # graf veritabanı + karar modeli .env'den, sentez için Llama
print(pipeline.query("What caused the 2008 financial crisis?"))
```

#### 4. Laya checkpoint'i seçin

Laya resmi [`laya`](https://pypi.org/project/laya/) paketi üzerinden yüklenir. Checkpoint'i `.env` içinde seçin:

```bash
LAYA_MODEL_ID=convaiinnovations/laya
LAYA_MODEL_SUBFOLDER=multilingual     # varsayılan: mmBERT-base, 322M, 100+ dil (Türkçe dahil)
# LAYA_MODEL_SUBFOLDER=               # İngilizce, ModernBERT-large, 421M
# LAYA_MODEL_SUBFOLDER=typed-decisions # 4 sentetik iş akışı için İngilizce uzman model
LAYA_DEVICE=                          # cuda | cpu, boş bırakılırsa otomatik algılanır
```

Aynı hızlı başlangıç, CPU, iki checkpoint:

| Checkpoint | Budanan halüsinasyon kenarları | Cevaplanan sorular (doğrulanmış / işaretli / çekimser) | Ingestion |
|------------|--------------------------------|--------------------------------------------------------|-----------|
| `multilingual` (varsayılan) | 2 / 2 | 1 / 1 / 1 | ~25 sn |
| İngilizce (kök) | 2 / 2 | 1 / 0 / 2 | ~50 sn |

Multilingual checkpoint bu örnekte hem daha iyi sonuç verdi hem de iki kat daha hızlıydı; varsayılan olmasının nedeni bu.

#### 5. Ablation düzeneğini çalıştırın

```python
from graphrag.models.ablation import AblationHarness

harness = AblationHarness()
result = harness.compare(
    context="Isaac Newton published Principia Mathematica in 1687.",
    instruction="Score the historical significance of this event.",
)
print(f"Laya: {result['laya']['score']:.3f} @ {result['laya']['latency_ms']:.0f}ms")
print(f"Jev:  {result['jev']['score']:.3f}  @ {result['jev']['latency_ms']:.0f}ms")
print(f"Δ:    {result['delta']:.4f}")
```

### Laya neyi yargılayabilir, neyi yargılayamaz

Laya bir System One karar modelidir. **Ona verdiğiniz durumu** yargılar; kendine ait bir dünya bilgisi yoktur. Pratikte bunun anlamı:
- **Kenarları tek başına değil, kaynak metinleriyle birlikte doğrulayın** (`EdgeVerifier.verify_against_source`). Kanıt olmadan "*Newton → born in → Woolsthorpe* geçerli mi?" diye sorulduğunda Laya bunu *Newton → baked → Banana Bread* ile hemen hemen aynı puanlar. Kaynak parça verildiğinde ikisini net biçimde ayırır (0,996'ya karşı 0,000).
- **Kanıt kontrolleri desteklenmeyen varlıkları yakalar, her yanlış ilişkiyi değil.** Hedefi kaynakta hiç geçmeyen bir üçlü (`born in Paris`, P≈0,01) reddedilir. İki varlığın da kaynakta geçtiği yanlış bir ilişki (`Marie Curie invented Warsaw`, P≈0,98) gözden kaçabilir.
- **Eşikleri ayarlanabilir kabul edin.** Model kartı olasılıkların aşırı özgüvenli olduğunu ve henüz kalibre edilmediğini belirtir. `BFS_PRUNE_THRESHOLD`, `SEED_FINAL_TOP_K` ve `graphrag/retrieval/post_traversal.py` içindeki kapı/kaynak kontrolü eşiklerini kendi verinize göre ayarlayın.

### Bilinen sınırlar

- **Ontoloji hizalaması hatasız değil:** Örnekte 12 ilişkiden 3'ü yanlış tipe gidiyor (ör. `"came up with"` → `BORN_IN`, `"formulated"` → `DISCOVERED`, `"introduced the law of"` → `RELATED_TO`). Alanınıza göre yazılmış küçük ve açıklayıcı bir şema bu oranı düşürür.
- **Çok adımlı sorular kapıda takılabilir:** Newton–Einstein sorusu doğru stratejiye yönlendiriliyor, ama halüsinasyon kapısı bağlamı yetersiz bulup cevap vermiyor. Bu, tahmin yürütmek yerine çekimser kalan tasarımın bilinçli bir sonucudur.
- **Kaynak kontrolü eşiği sıkı:** 0,90 eşiği, Türkçe soruda doğru bulunan olguyu 0,87 ile `[⚠️ UNVERIFIED]` olarak işaretletti. Kendi verinizle ayarlayın.
- **Kùzu embedding'leri bellekte:** Grafı kuran ve sorgulayan kod aynı süreçte olmalıdır (bkz. Kùzu notu).
- **`--llm llama` yolu GPU gerektirir:** Llama-3.1-8B NF4 için CUDA ve bitsandbytes gerekir. Yukarıdaki sonuçlar GPU'suz bir makinede, extractive sentezleyiciyle alınmıştır; Llama yolu orada test edilmemiştir.

---

## 📁 Proje Yapısı

```
graphrag_neo4j_laya/
├── graphrag/
│   ├── graph/                    # Veritabanı soyutlama katmanı
│   │   ├── base.py               # BaseGraphClient ABC
│   │   ├── factory.py            # GRAPH_DB_TYPE seçici
│   │   ├── neo4j_client.py       # Neo4j (Bolt + GDS)
│   │   ├── memgraph_client.py    # Memgraph (Bolt)
│   │   ├── age_client.py         # Apache AGE (PostgreSQL)
│   │   └── kuzu_client.py        # Kùzu (gömülü)
│   ├── models/                   # Yapay zekâ karar katmanı
│   │   ├── base_decision.py      # BaseDecisionModel ABC (Score/Noul/Choice)
│   │   ├── laya.py               # `laya` paketi üzerinden yerel Laya modeli
│   │   ├── jev.py                # TypeSafe Jev API istemcisi
│   │   ├── ablation.py           # Yan yana karşılaştırma düzeneği
│   │   └── decision_factory.py   # DECISION_MODEL_BACKEND seçici
│   ├── ingestion/                # Faz 1: çevrimdışı pipeline
│   │   ├── chunker.py            # Noul sınır tabanlı parçalama
│   │   ├── entity_extractor.py   # LLM NER + Noul ayrıştırma
│   │   ├── edge_verifier.py      # Kenar doğrulama ve budama
│   │   ├── ontology_aligner.py   # Choice tabanlı şema hizalama
│   │   └── community.py          # Leiden topluluk tespiti
│   ├── retrieval/                # Faz 2-4: sorgu pipeline'ı
│   │   ├── router.py             # Choice: niyet yönlendirme
│   │   ├── seed_selector.py      # Score: seed doğrulama
│   │   ├── post_traversal.py     # Score/Choice/Noul: son işleme
│   │   └── traversal/
│   │       ├── astar.py          # A* semantik traversal + erken çıkış
│   │       └── bfs.py            # Score kapılı BFS (local niyet)
│   ├── benchmarks/               # Performans analizi
│   │   ├── hop_latency.py        # Tüm backend'lerde adım başına veritabanı gecikmesi
│   │   ├── laya_vs_jev.py        # Yan yana model karşılaştırması
│   │   ├── greedy_vs_astar.py    # Arama stratejisi karşılaştırması
│   │   └── vram_monitor.py       # GPU bellek takibi
│   └── pipeline.py               # Uçtan uca orkestratör
├── examples/
│   ├── laya_kuzu_quickstart.py   # Uçtan uca demo: Laya + Kùzu, Docker yok
│   └── data/science_history.json # Demo veri seti (dokümanlar, üçlüler, şema, sorular)
├── config/
│   ├── settings.py               # Pydantic ayarları (tüm eşikler)
│   └── .env.example              # Tüm seçenekleri içeren şablon
└── requirements.txt
```

---

## 🔬 Ablation Modu: RLCD Veri Fabrikası

`DECISION_MODEL_BACKEND=ablation` ayarlandığında her primitif çağrısı için Laya ve Jev arka planda paralel çalıştırılır ve sonuçlar bir JSONL dosyasına kaydedilir:

```json
{"primitive": "score", "context": "...", "instruction": "...",
 "laya": {"score": 0.83, "latency_ms": 34.1},
 "jev":  {"score": 0.79, "latency_ms": 48.3},
 "delta": 0.04}
```

Bu JSONL kaydı, Laya'yı Jev seviyesinde zero-shot doğruluğa taşımak için fine-tune etmeye hazır RLCD eğitim verisidir; tamamen yerel, üst seviye kalitede bir GraphRAG motoruna giden yol budur.

---

## 📊 Performans

| Backend | Kenar Puanlama Gecikmesi | Traversal (4 adım) | VRAM |
|---------|--------------------------|--------------------|------|
| Laya (RTX 5060 FP16) | ~33 ms | ~150 ms | ~1,2 GB |
| Jev (bulut API) | ~50 ms | ~220 ms | 0 |
| Adım başına LLM (referans) | ~2.000 ms | ~15.000 ms | ~6 GB |

---

## 🤝 Katkıda Bulunma

Katkılarınızı bekliyoruz:
- Ek graf veritabanı bağlayıcıları (Nebula, TigerGraph, FalkorDB vb.)
- Ablation JSONL kayıtlarından Laya fine-tuning script'leri
- Jev için async/streaming desteği
- Yeni ingestion kaynakları (PDF, HTML, Markdown)

---

## 📄 Lisans

Apache 2.0 Lisansı ile lisanslanmıştır.

Üzerine inşa edildiği projeler:
- [Laya](https://huggingface.co/convaiinnovations/laya) (Apache 2.0, mmBERT / ModernBERT)
- [TypeSafe Jev API](https://typesafe.ai)
- [Neo4j](https://neo4j.com) · [Memgraph](https://memgraph.com) · [Apache AGE](https://age.apache.org) · [Kùzu](https://kuzudb.com)
