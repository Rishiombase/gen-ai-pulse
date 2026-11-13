"""Central configuration: site settings, sources, and category taxonomy.

Everything the pipeline needs to know about *what* to collect lives here, so
adding a source or tuning a category never requires touching pipeline logic.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"                 # persisted archive (committed to git)
ARCHIVE_DIR = DATA_DIR / "articles"      # one JSON file per publication month
STATE_DIR = DATA_DIR / "state"           # http cache, source health, run metadata
SITE_DIR = ROOT / "site"                 # static site root (deployed)
SITE_DATA_DIR = SITE_DIR / "data"        # generated JSON consumed by the frontend
SITE_NEWSLETTER_DIR = SITE_DIR / "newsletters"  # generated PDFs
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

SITE = {
    "name": os.environ.get("SITE_NAME", "Gen AI Pulse"),
    "org": "AIT Global",
    "tagline": "The AI technology your business should know about.",
    # Used for links printed in the PDF newsletters. Set SITE_URL in CI.
    "url": os.environ.get("SITE_URL", "https://example.github.io/ait-newsletter/"),
}

USER_AGENT = "Mozilla/5.0 (compatible; AITNewsBot/1.0; +https://github.com/ait-newsletter)"
HTTP_TIMEOUT = 20          # seconds per request
MAX_WORKERS = 8            # concurrent source fetches

# On the very first run the archive is empty; accept items up to this old so
# feeds with long histories can seed the previous quarter's newsletter.
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "140"))
# On regular runs, ignore items older than this (feeds sometimes resurface old posts).
REGULAR_MAX_AGE_DAYS = 21
# Keep this many days in the dashboard's "latest" payload.
LATEST_WINDOW_DAYS = 45
# Same-story clustering only compares items published this close together.
CLUSTER_WINDOW_DAYS = 4

# ---------------------------------------------------------------------------
# Source credibility tiers. The tier feeds the importance score and is shown
# to readers, so it must reflect how authoritative the outlet is for AI news.
# ---------------------------------------------------------------------------
TIERS = {
    1: {"label": "Primary source", "weight": 30,
        "desc": "Official announcements from AI labs and companies."},
    2: {"label": "Research institution", "weight": 24,
        "desc": "University labs, corporate research groups, curated paper rankings."},
    3: {"label": "Established press", "weight": 20,
        "desc": "Technology newsrooms with editorial standards."},
    4: {"label": "Specialist analysis", "weight": 14,
        "desc": "Expert newsletters, practitioner blogs, and developer blogs."},
    5: {"label": "Preprint", "weight": 8,
        "desc": "arXiv preprints. Not peer reviewed."},
}

# kind: "news" (announcements and reporting) or "paper" (research papers)
# filter_ai: True when the feed covers more than AI and needs relevance filtering
# limit: maximum items taken per run (protects against very long feeds)
SOURCES: list[dict] = [
    # --- Tier 1: AI labs & companies (primary sources) ----------------------
    {"id": "openai", "name": "OpenAI", "type": "rss", "tier": 1,
     "url": "https://openai.com/news/rss.xml", "home": "https://openai.com/news/"},
    {"id": "anthropic", "name": "Anthropic", "type": "sitemap", "tier": 1,
     "url": "https://www.anthropic.com/sitemap.xml", "home": "https://www.anthropic.com/news",
     "include": [r"^https://www\.anthropic\.com/(news|research)/[^/]+/?$"], "limit": 40},
    {"id": "deepmind", "name": "Google DeepMind", "type": "rss", "tier": 1, "org": "google",
     "url": "https://deepmind.google/blog/rss.xml", "home": "https://deepmind.google/discover/blog/"},
    {"id": "google-ai", "name": "Google AI", "type": "rss", "tier": 1, "org": "google",
     "url": "https://blog.google/innovation-and-ai/technology/ai/rss/", "home": "https://blog.google/technology/ai/"},
    {"id": "microsoft-ai", "name": "Microsoft", "type": "rss", "tier": 1, "org": "microsoft",
     "url": "https://news.microsoft.com/source/topics/ai/feed/", "home": "https://news.microsoft.com/source/topics/ai/"},
    {"id": "nvidia", "name": "NVIDIA", "type": "rss", "tier": 1, "org": "nvidia", "filter_ai": True,
     "url": "https://blogs.nvidia.com/feed/", "home": "https://blogs.nvidia.com/"},
    {"id": "huggingface", "name": "Hugging Face", "type": "rss", "tier": 1, "org": "huggingface",
     "url": "https://huggingface.co/blog/feed.xml", "home": "https://huggingface.co/blog"},
    {"id": "meta-eng", "name": "Meta Engineering", "type": "rss", "tier": 1, "filter_ai": True,
     "url": "https://engineering.fb.com/feed/", "home": "https://engineering.fb.com/"},
    {"id": "apple-ml", "name": "Apple Machine Learning Research", "type": "rss", "tier": 1, "hint": "research",
     "url": "https://machinelearning.apple.com/rss.xml", "home": "https://machinelearning.apple.com/"},
    {"id": "github-ai", "name": "GitHub", "type": "rss", "tier": 1,
     "url": "https://github.blog/ai-and-ml/feed/", "home": "https://github.blog/ai-and-ml/"},
    {"id": "pytorch", "name": "PyTorch", "type": "rss", "tier": 1,
     "url": "https://pytorch.org/feed/", "home": "https://pytorch.org/blog/"},

    # --- Tier 2: research groups & curated research --------------------------
    {"id": "google-research", "name": "Google Research", "type": "rss", "tier": 2, "hint": "research", "org": "google",
     "url": "https://research.google/blog/rss/", "home": "https://research.google/blog/"},
    {"id": "msr", "name": "Microsoft Research", "type": "rss", "tier": 2, "hint": "research", "org": "microsoft", "filter_ai": True,
     "url": "https://www.microsoft.com/en-us/research/feed/", "home": "https://www.microsoft.com/en-us/research/blog/"},
    {"id": "bair", "name": "Berkeley AI Research", "type": "rss", "tier": 2, "hint": "research",
     "url": "https://bair.berkeley.edu/blog/feed.xml", "home": "https://bair.berkeley.edu/blog/"},
    {"id": "mit-news-ai", "name": "MIT News", "type": "rss", "tier": 2, "org": "mit",
     "url": "https://news.mit.edu/rss/topic/artificial-intelligence2", "home": "https://news.mit.edu/topic/artificial-intelligence2"},
    {"id": "hf-papers", "name": "Hugging Face Daily Papers", "type": "hf_papers", "tier": 2, "org": "huggingface",
     "kind": "paper", "url": "https://huggingface.co/api/daily_papers", "home": "https://huggingface.co/papers"},

    # --- Tier 3: established technology press --------------------------------
    {"id": "techcrunch", "name": "TechCrunch", "type": "rss", "tier": 3,
     "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "home": "https://techcrunch.com/category/artificial-intelligence/"},
    {"id": "verge", "name": "The Verge", "type": "rss", "tier": 3,
     "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "home": "https://www.theverge.com/ai-artificial-intelligence"},
    {"id": "mit-tr", "name": "MIT Technology Review", "type": "rss", "tier": 3, "org": "mit",
     "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "home": "https://www.technologyreview.com/topic/artificial-intelligence/"},
    {"id": "ars", "name": "Ars Technica", "type": "rss", "tier": 3,
     "url": "https://arstechnica.com/ai/feed/", "home": "https://arstechnica.com/ai/"},
    {"id": "wired", "name": "Wired", "type": "rss", "tier": 3,
     "url": "https://www.wired.com/feed/tag/ai/latest/rss", "home": "https://www.wired.com/tag/artificial-intelligence/"},
    {"id": "ieee", "name": "IEEE Spectrum", "type": "rss", "tier": 3,
     "url": "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", "home": "https://spectrum.ieee.org/topic/artificial-intelligence/"},

    # --- Tier 4: specialist analysis & developer blogs -----------------------
    {"id": "nvidia-dev", "name": "NVIDIA Developer", "type": "rss", "tier": 4, "org": "nvidia",
     "url": "https://developer.nvidia.com/blog/feed", "home": "https://developer.nvidia.com/blog/"},
    {"id": "aws-ml", "name": "AWS Machine Learning", "type": "rss", "tier": 4,
     "url": "https://aws.amazon.com/blogs/machine-learning/feed/", "home": "https://aws.amazon.com/blogs/machine-learning/"},
    {"id": "import-ai", "name": "Import AI", "type": "rss", "tier": 4, "press": True,
     "url": "https://importai.substack.com/feed", "home": "https://importai.substack.com/"},
    {"id": "simon-willison", "name": "Simon Willison", "type": "rss", "tier": 4, "press": True, "filter_ai": True,
     "url": "https://simonwillison.net/atom/everything/", "home": "https://simonwillison.net/"},
    {"id": "last-week-ai", "name": "Last Week in AI", "type": "rss", "tier": 4, "press": True,
     "url": "https://lastweekin.ai/feed", "home": "https://lastweekin.ai/"},
    {"id": "decoder", "name": "The Decoder", "type": "rss", "tier": 4, "press": True,
     "url": "https://the-decoder.com/feed/", "home": "https://the-decoder.com/"},
    {"id": "marktechpost", "name": "MarkTechPost", "type": "rss", "tier": 4, "press": True,
     "url": "https://www.marktechpost.com/feed/", "home": "https://www.marktechpost.com/"},

    # --- Tier 5: preprints ---------------------------------------------------
    {"id": "arxiv", "name": "arXiv", "type": "arxiv", "tier": 5, "kind": "paper",
     "categories": ["cs.AI", "cs.CL", "cs.LG", "cs.CV", "cs.RO", "cs.MA"], "limit": 30,
     "url": "https://export.arxiv.org/api/query", "home": "https://arxiv.org/"},
]

SOURCE_BY_ID = {s["id"]: s for s in SOURCES}

# ---------------------------------------------------------------------------
# Category taxonomy. Each category has:
#   keywords:   regex fragments with weights (title matches count double)
#   prototypes: plain-language descriptions used by the embedding classifier
# ---------------------------------------------------------------------------
CATEGORIES: list[dict] = [
    {"id": "genai-llm", "name": "Generative AI & LLMs", "color": "#6D3FD6",
     "keywords": {r"\bllms?\b": 3, r"large language models?": 3, r"\bgpt[- ]?\d": 3, r"\bchatgpt\b": 3,
                  r"\bclaude\b": 3, r"\bgemini\b": 3, r"\bllama\b": 2, r"\bgenerative\b": 2,
                  r"\bfoundation models?\b": 2, r"\bchatbots?\b": 2, r"\btext-to-(image|video|speech)\b": 2,
                  r"\bdiffusion\b": 1.5, r"\bprompt(s|ing)?\b": 1.5, r"\breasoning models?\b": 2.5,
                  r"\bfine-?tun(e|ing)\b": 1.5, r"\btokens?\b": 1, r"\bcontext window\b": 2,
                  r"\bmultimodal\b": 1.5, r"\bsora\b|\bveo\b|\bimagen\b|\bmidjourney\b": 2, r"\bcopilot\b": 1.5,
                  r"\bgrok\b|\bqwen\b|\bdeepseek\b|\bmistral\b|\bgemma\b|\bphi-\d": 2},
     "prototypes": ["A new large language model or chatbot is released or updated.",
                    "Generative AI models that create text, images, audio, or video.",
                    "Improvements to LLM reasoning, context length, or prompting."]},
    {"id": "agents", "name": "AI Agents & Multi-Agent Systems", "color": "#0E8A6A",
     "keywords": {r"\bagents?\b": 3, r"\bagentic\b": 3.5, r"multi-agent": 4, r"\btool (use|calling)\b": 2.5,
                  r"\bcomputer use\b": 3, r"\bautonomous\b": 1.5, r"\bworkflows?\b": 1, r"\borchestrat": 1.5,
                  r"\bmcp\b|model context protocol": 3, r"\bcoding assistants?\b|\bcoding agents?\b": 3,
                  r"\bcodex\b|claude code": 2.5, r"\bbrowser agents?\b": 3},
     "prototypes": ["AI agents that autonomously take actions, use tools, and complete multi-step tasks.",
                    "Multi-agent systems where several AI agents coordinate and collaborate.",
                    "Coding agents and agent frameworks for software automation."]},
    {"id": "research", "name": "AI Research & Breakthroughs", "color": "#1F5FBF",
     "keywords": {r"\bpaper\b": 2, r"\bresearchers?\b": 2, r"\bstudy\b": 1.5, r"\bbenchmark": 2,
                  r"state[- ]of[- ]the[- ]art": 2.5, r"\bbreakthrough": 3, r"\bnovel\b": 1.5,
                  r"\barxiv\b": 2, r"\bwe propose\b|\bwe introduce\b|\bwe present\b": 3, r"\bdataset\b": 1.5,
                  r"\btheorem|\bproofs?\b|\bmathematic": 1.5, r"\binterpretability\b": 2.5,
                  r"\bscientific discovery\b|\bprotein|\balphafold\b": 2.5, r"\bneurips\b|\bicml\b|\biclr\b|\bcvpr\b|\bacl\b": 2.5},
     "prototypes": ["A scientific research paper presenting a new machine learning method and experiments.",
                    "Researchers report a breakthrough result or new state-of-the-art benchmark.",
                    "AI used for scientific discovery in biology, physics, or mathematics."]},
    {"id": "tools-launches", "name": "AI Tools & Product Launches", "color": "#C2410C",
     "keywords": {r"\blaunch(es|ed|ing)?\b": 3, r"\bintroduc(es|ing|ed)\b": 2.5, r"\bnow available\b": 3,
                  r"\brolls? out\b|\brolling out\b": 3, r"\bunveil": 3, r"\brelease[sd]?\b": 2,
                  r"\bnew features?\b": 2.5, r"\bapp\b|\bapps\b": 1.5, r"\bapi\b": 1.5, r"\bplugin|\bextension": 1.5,
                  r"\bgeneral availability\b|\bin preview\b|\bbeta\b": 2.5, r"\bpricing\b|\bsubscription": 1.5,
                  r"\bupdate[sd]?\b": 1},
     "prototypes": ["A company launches a new AI product, app, feature, or API for users.",
                    "A new AI tool becomes generally available or rolls out to customers.",
                    "Product update announcement with new capabilities and pricing."]},
    {"id": "computer-vision", "name": "Computer Vision", "color": "#B4237A",
     "keywords": {r"\bcomputer vision\b": 4, r"\bimages?\b": 1.5, r"\bvideo\b": 1.5, r"\bvision\b": 2,
                  r"\bobject detection\b|\bsegmentation\b": 3.5, r"\bvision[- ]language\b|\bvlms?\b": 3,
                  r"\b3d\b|\bpoint clouds?\b|\bnerf\b|gaussian splat": 2.5, r"\bimage generation\b": 2,
                  r"\bocr\b|\bdepth estimation\b|\bpose estimation\b": 3, r"\bcameras?\b": 1, r"\bpixels?\b": 1},
     "prototypes": ["Computer vision models for image recognition, detection, and segmentation.",
                    "Vision-language models that understand images and video.",
                    "3D reconstruction, video understanding, and visual perception research."]},
    {"id": "nlp", "name": "Natural Language Processing", "color": "#7A5A00",
     "keywords": {r"natural language": 3.5, r"\bnlp\b": 4, r"\btranslat(e|ion)\b": 3, r"\blanguages?\b": 1.5,
                  r"\bspeech\b|\bvoice\b|\basr\b|\btext-to-speech\b|\btts\b": 2.5, r"\bsentiment\b": 3,
                  r"\bsummariz": 2, r"\bmultilingual\b|\blow-resource\b": 3, r"\bretrieval\b|\brag\b": 2,
                  r"\btokeniz": 2, r"\bquestion answering\b": 3, r"\bembeddings?\b": 1.5},
     "prototypes": ["Natural language processing: translation, summarization, question answering.",
                    "Speech recognition, voice assistants, and text-to-speech systems.",
                    "Multilingual language models and retrieval-augmented generation."]},
    {"id": "robotics", "name": "Robotics & Automation", "color": "#4D6B12",
     "keywords": {r"\brobot": 4, r"\bhumanoids?\b": 4, r"\bautonomous (vehicles?|driving|cars?)\b": 4,
                  r"\bself-driving\b|\brobotaxi": 4, r"\bdrones?\b": 3, r"\bembodied\b": 3.5,
                  r"\bmanipulation\b|\bgrasp": 3, r"\blocomotion\b": 3, r"\bwaymo\b|\btesla\b|\bfigure ai\b": 2,
                  r"\bfactory|\bwarehouse|\bindustrial automation\b": 2, r"\bphysical ai\b|\bsim-to-real\b": 3.5},
     "prototypes": ["Robots, humanoid robots, and robotic manipulation powered by AI.",
                    "Self-driving cars, drones, and autonomous vehicles.",
                    "Embodied AI and physical automation in factories and warehouses."]},
    {"id": "enterprise", "name": "Enterprise AI & Use Cases", "color": "#9A3412",
     "keywords": {r"\benterprises?\b": 3.5, r"\bbusiness(es)?\b": 2, r"\bcustomers?\b": 2, r"\bcase stud(y|ies)\b": 4,
                  r"\buse cases?\b": 4, r"\bin production\b|\bdeploy(ed|s|ing|ment)\b": 2.5, r"\badopt(s|ed|ion)\b": 2.5,
                  r"\bworkflows?\b|\bautomat(e|es|ed|ion)\b": 2, r"\bproductivity\b|\befficiency\b|\broi\b": 2.5,
                  r"\bcustomer (service|support|experience)\b|\bcontact cent(er|re)\b": 3.5, r"\bintegrat(e|es|ed|ion)s?\b": 2,
                  r"\b(healthcare|clinical|medical|finance|financial services|banking|insurance|retail|manufacturing|"
                  r"legal|law firms?|supply chain|telecom|logistics|pharma\w*)\b": 3,
                  r"^how .{3,60} (uses?|is using|built|builds|scales?|governs?|is developing|transformed|deploys?)\b": 3.5,
                  r"\bfor (businesses|teams|companies|organi[sz]ations)\b": 3.5, r"\bpartners? with\b|\bpartnership\b": 1.5},
     "prototypes": ["How a company uses AI in production: a customer case study and the results it achieved.",
                    "AI solutions for customer support, finance, healthcare, legal, retail, and manufacturing.",
                    "Enterprise AI platforms, integrations, and workflow automation for business teams."]},
    {"id": "security", "name": "AI Safety, Security & Governance", "color": "#334155",
     "keywords": {r"\bsecurity\b|\bsecure\b": 3, r"\bvulnerab\w*|\bexploit\w*|\bcve-": 4, r"\bjailbreak\w*": 4,
                  r"\bprompt injection\b": 4.5, r"\bred[- ]team\w*": 4, r"\bsafety\b": 3, r"\balignment\b": 3,
                  r"\bguardrails?\b": 4, r"\bprivacy\b|\bdata protection\b|\bpii\b": 3, r"\bcompliance\b|\bcompliant\b": 3.5,
                  r"\bgovernance\b|\baudit\w*": 3.5, r"\bwatermark\w*|\bprovenance\b|\bc2pa\b": 3.5,
                  r"\bdeepfake detection\b|\bdetect(ing|ion) (ai|synthetic)": 3, r"\bai act\b|\biso 42001\b|\bnist\b": 3,
                  r"\brisks?\b|\bthreats?\b|\bmalware\b|\bcyber\w*": 2.5, r"\bevals?\b|\bevaluations?\b": 1.5,
                  r"\binterpretability\b|\bhallucinat\w*": 2.5},
     "prototypes": ["AI security: prompt injection, jailbreaks, model vulnerabilities, and how to defend against them.",
                    "AI safety research, alignment, evaluations, hallucination, and guardrails for models.",
                    "Governance, privacy, and compliance requirements companies must meet when deploying AI."]},
    {"id": "open-source", "name": "Open-Source AI Models", "color": "#0F766E",
     "keywords": {r"\bopen[- ]source\b": 4, r"\bopen[- ]weights?\b": 4.5, r"\bopen models?\b": 4,
                  r"\bhugging ?face\b": 2.5, r"\bgithub\b": 1.5, r"\blicen[cs]e\b": 2, r"\bapache 2\.0\b|\bmit license\b": 3,
                  r"\bllama\b|\bmistral\b|\bqwen\b|\bdeepseek\b|\bgemma\b|\bolmo\b|\bphi-\d": 2.5,
                  r"\bcheckpoints?\b": 2, r"\bgguf\b|\bollama\b|\bllama\.cpp\b|\bvllm\b": 3, r"\bcommunity\b": 1},
     "prototypes": ["An open-source or open-weights AI model is released for anyone to download.",
                    "Open-source AI libraries, datasets, and community model releases on Hugging Face.",
                    "Running open models locally with community tools."]},
    {"id": "mlops", "name": "MLOps & AI Infrastructure", "color": "#475569",
     "keywords": {r"\bgpus?\b": 3, r"\bdata ?cent(er|re)s?\b": 3.5, r"\bchips?\b|\bsemiconductor": 3,
                  r"\binference\b": 2.5, r"\btraining (cluster|infrastructure|run)": 3.5, r"\bmlops\b": 4,
                  r"\bkubernetes\b|\bdeploy(ment|ing)?\b": 2, r"\blatency\b|\bthroughput\b": 2.5,
                  r"\bquantiz": 3, r"\bcuda\b|\btensorrt\b|\btpus?\b|\bblackwell\b|\bh100\b|\bh200\b|\bgb200\b": 3,
                  r"\bcompute\b": 2, r"\bcloud\b": 1.5, r"\bsagemaker\b|\bbedrock\b|\bvertex ai\b|\bazure\b": 2,
                  r"\bserving\b|\bpipelines?\b|\bobservability\b|\bevals?\b": 1.5, r"\bpower\b|\benergy\b": 1.5},
     "prototypes": ["AI infrastructure: GPUs, chips, data centers, and compute capacity.",
                    "Deploying, serving, and optimizing machine learning models in production.",
                    "MLOps tooling for training pipelines, inference efficiency, and monitoring."]},
    {"id": "ai-ml", "name": "AI & Machine Learning", "color": "#2B47D9",
     "keywords": {r"\bmachine learning\b": 2.5, r"\bartificial intelligence\b": 2, r"\bdeep learning\b": 2.5,
                  r"\bneural networks?\b": 2.5, r"\breinforcement learning\b": 2.5, r"\bml\b": 1.5,
                  r"\bai\b": 0.5, r"\bmodels?\b": 0.5, r"\btraining\b": 1},
     "prototypes": ["General artificial intelligence and machine learning news.",
                    "Machine learning techniques like deep learning and reinforcement learning."]},
]

CATEGORY_BY_ID = {c["id"]: c for c in CATEGORIES}
FALLBACK_CATEGORY = "ai-ml"

# Used to decide whether an item from a mixed-topic feed is about AI at all.
AI_RELEVANCE_PATTERN = (
    r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|deep learning|neural|llms?|"
    r"language models?|gpt|chatgpt|claude|gemini|llama|generative|agents?|agentic|copilot|"
    r"inference|training|transformer|diffusion|robot\w*|computer vision|nlp|cuda|gpus?|"
    r"models?)\b"
)

# Phrases that indicate high-signal news (used by the importance score).
SIGNAL_PATTERNS = {
    r"\b(launch(es|ed)?|introduc(es|ing)|unveil(s|ed)?|announc(es|ed|ing))\b": 5,
    r"\b(releases?|released|now available|rolls? out|general availability|available today)\b": 5,
    r"\bopen[- ](source|weights?)\b": 5,
    r"\b(apis?|sdks?|developers?)\b": 3,
    r"\b(state[- ]of[- ]the[- ]art|breakthrough|benchmark|outperforms?|faster|cheaper)\b": 3,
    r"\b(gpt[- ]?\d[\w.]*|claude [\w.]+|gemini [\w.]+|llama ?\d[\w.]*|qwen ?[\d.]+|o\d\b)": 4,
    r"\b(model|agent|framework|platform|tool)s?\b": 2,
}
NOISE_PATTERNS = {
    r"\b(how to|tutorial|step[- ]by[- ]step|getting started|webinar|podcast|livestream|"
    r"sponsored|week in review|recap|roundup|newsletter #?\d+|last week in ai #?\d+|"
    r"the latest ai news|import ai \d+|lwiai)\b": 10,
    r"\b(deals?|discount|sale|% off|black friday)\b": 10,
}

# Recognised entities used for "trending topics" (display name -> regex).
ENTITIES = {
    "OpenAI": r"\bopenai\b", "ChatGPT": r"\bchatgpt\b", "GPT": r"\bgpt[- ]?\d",
    "Anthropic": r"\banthropic\b", "Claude": r"\bclaude\b", "Gemini": r"\bgemini\b", "DeepMind": r"\bdeepmind\b",
    "Llama": r"\bllama\b", "Microsoft": r"\bmicrosoft\b", "Copilot": r"\bcopilot\b", "NVIDIA": r"\bnvidia\b",
    "AWS": r"\baws\b|\bamazon bedrock\b|\bsagemaker\b", "Mistral": r"\bmistral\b", "DeepSeek": r"\bdeepseek\b",
    "Qwen": r"\bqwen\b", "Hugging Face": r"\bhugging ?face\b", "Grok": r"\bgrok\b",
    "AI agents": r"\bagents?\b|\bagentic\b", "Open weights": r"\bopen[- ]weights?\b|\bopen[- ]source models?\b",
    "Reasoning": r"\breasoning\b", "Coding": r"\bcod(e|ing)\b|\bprogramm", "MCP": r"\bmcp\b|model context protocol",
    "RAG": r"\brag\b|\bretrieval\b", "Fine-tuning": r"\bfine-?tun", "Inference": r"\binference\b",
    "GPUs": r"\bgpus?\b|\bchips?\b|\bblackwell\b|\btpus?\b", "Data centers": r"\bdata ?cent(er|re)s?\b",
    "Video generation": r"\bvideo generation\b|\btext-to-video\b|\bsora\b|\bveo\b", "Speech": r"\bspeech\b|\bvoice\b|\btts\b|\basr\b",
    "Robotics": r"\brobot\w*|\bhumanoids?\b", "AI security": r"\bsecurity\b|\bprompt injection\b|\bjailbreak",
    "Multimodal": r"\bmultimodal\b|\bvision[- ]language\b", "Healthcare": r"\bhealth\w*|\bmedical\b|\bclinical\b",
    "Small models": r"\bsmall (language )?models?\b|\bslms?\b|\bon-device\b|\bedge\b",
}

# ---------------------------------------------------------------------------
# Editorial focus: technology that companies can use. Stories that are mainly
# about people, politics, lawsuits, or money are kept in the archive but hidden.
# ---------------------------------------------------------------------------
OFF_TOPIC_PATTERNS = {
    r"\b(resign\w*|steps? down|stepping down|ousted|ouster|fired|firing|successor|interim ceo|departs?|departure|"
    r"poach\w*|hires?|hired|joins .{0,30} as|appointed|named (ceo|cto|president))\b": 5,
    r"\b(scandal|affair|espionage|allegations?|accus\w+|controvers\w*|feud|drama|rumou?rs?|gossip|backlash|"
    r"vows?|slams?|blasts?|criticiz\w*|defend(s|ing)|lashes out|fires back|mocks?)\b": 5,
    r"\b(lawsuits?|sues?|sued|suing|court|judge|trial|verdict|settle(ment|s|d)?|indict\w*|investigat\w*|probe|"
    r"subpoena|antitrust|fine[ds]? by)\b": 4,
    r"\b(congress\w*|senate|senators?|lawmakers?|democrats?|republicans?|white house|trump|biden|elections?|politic\w*|"
    r"campaign|governor|parliament|prime minister|tariffs?|sanctions?|export controls?)\b": 4,
    r"\b(stocks?|shares?|share price|market cap|valuation|ipo|investors?|earnings|quarterly results|wall street|"
    r"raises?|raised|funding|funds?|series [a-f]|venture capital|vcs?|billionaire|net worth|bubble|"
    r"initial public offering|roadshow|acquisition|acquires?|merger)\b": 3.5,
    r"\b(layoffs?|job cuts|unions?|strikes?|protests?)\b": 3.5,
    r"\b(opinion|op-ed|column|essay|interview|podcast|profile|memoir|book review|letter)\b": 3,
    r"\b(who is|meet the|what happens next|could replace|what did .{0,20} want|the rise and fall)\b": 4,
    r"\b(deals?|discount|on sale|black friday|cyber monday|gift guide|best .{0,20} to buy|review:)\b": 4,
    r"\b(ceo|cto|cfo|founder|co-?founder|chairman|executives?|billionaire)\b": 2,
}
TECH_PATTERNS = {
    r"\b(models?|llms?|apis?|sdks?|open[- ](source|weights?)|benchmarks?|architecture|inference|training|fine-?tun\w*|"
    r"frameworks?|library|libraries|datasets?|algorithms?|parameters|latency|throughput|gpus?|tpus?|chips?)\b": 2,
    r"\b(launch(es|ed)?|releases?|released|introduc(es|ing)|now available|rolls? out|unveil\w*|update[sd]?|features?|"
    r"version|preview|beta)\b": 2,
    r"\b(agents?|agentic|tools?|platform|feature|research|paper|study|technique|method|system|pipeline|workflow)\b": 1.5,
    r"\bv?\d+\.\d+(\.\d+)?\b": 1.5,
}
TECH_PROTOTYPES = [
    "A new AI model, tool, API, or product feature is released for developers and companies.",
    "Technical research results, benchmarks, and engineering details of an AI system.",
    "How to build, deploy, and use AI technology in real products and business workflows.",
    "New AI infrastructure, hardware, and software that makes models faster or cheaper to run.",
]
OFF_TOPIC_PROTOTYPES = [
    "News about a CEO, executive, or famous person: resignations, hiring, scandals, and personal life.",
    "Politics, lawsuits, court cases, and government investigations involving technology companies.",
    "Stock prices, company valuations, investors, and startup funding rounds.",
    "An opinion column or interview about society, culture, and the future of work.",
]

# "How can we use it" badges. First match wins its slot; at most three per story.
USAGE_TAGS = {
    "Open source": r"\bopen[- ]source\b|\bopen[- ]weights?\b|\bapache 2\.0\b|\bmit licen[cs]e\b|\bon hugging ?face\b|\bon github\b",
    "API": r"\bapis?\b|\bsdks?\b",
    "Available now": r"\bnow available\b|\bgenerally available\b|\bavailable (today|now)\b|\brolls? out\b|\brolling out\b|"
                     r"\blaunch(es|ed)\b|\bintroduc(es|ing)\b|\breleases?\b|\breleased\b|\bships?\b",
    "Preview": r"\bpreview\b|\bbeta\b|\bearly access\b|\bwaitlist\b|\bexperimental\b",
    "Enterprise": r"\benterprises?\b|\bfor (businesses|teams|companies|organi[sz]ations)\b|\bin production\b|\bcase study\b",
    "For developers": r"\bdevelopers?\b|\bframework\b|\blibrary\b|\bcli\b|\bopen-source tool\b",
}
