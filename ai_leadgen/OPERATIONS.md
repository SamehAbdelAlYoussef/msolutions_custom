# AI Lead Gen — دليل التشغيل الكامل (الموديول + المحرك + السيرفر)

> آخر تحديث: 2026-11-19. هذا الملف هو المرجع الوحيد لكل ما يخص النظام: ما هو، أين يعمل، كيف يُراقَب، كيف يُصلَح عند أي عطل.
> **لا توجد هنا أي كلمات سر أو مفاتيح أو عناوين IP**، لأن الريبو الذي فيه هذا الملف **عام (public)**. أماكن الأسرار مذكورة بالاسم فقط.

---

## 1) الفكرة في دقيقة

النظام من **جزأين منفصلين**:

| الجزء | هو إيه | أين يعمل |
|---|---|---|
| **الموديول `ai_leadgen`** | لوحة التحكم داخل Odoo: القوائم، الإعدادات، قائمة الجروبات، لوحة العمليات، حقول الـ CRM | داخل Odoo، مثبّت على قاعدة `sameh` فقط |
| **المحرك (engine)** | برنامج Python هو اللي بيبحث فعلًا: يقرأ فيسبوك وتليجرام والخرائط، يطلب من Gemini الحكم على كل طلب، ثم يفتح lead في الـ CRM | **حاوية Docker منفصلة** اسمها `leadgen_engine` على نفس السيرفر |

**الموديول لوحده لا يجيب أي lead.** الـ leads يصنعها المحرك ويكتبها في CRM قاعدة `sameh` عبر XML-RPC بمفتاح API.

```
 فيسبوك (10 جروبات) ─┐
 تليجرام (البوت)     ─┼─►  حاوية leadgen_engine ──(1) تحكم Gemini ──► lead في CRM (sameh)
 خرائط OpenStreetMap ─┘          │  ▲                                  ▲
                                  │  └─ كل 5 دقائق يسحب الجروبات من ───┤  (XML-RPC)
                                  └─ كل 5 دقائق يبعت التقرير ──────────► لوحة Operations
```

نطاق الاستهداف: **الدول العربية الـ 22** (مصر ضمنها). فلتر الدولة يُطبَّق على تليجرام والخرائط، **ولا يُطبَّق على فيسبوك** (الجروبات التي تختارها أنت هي الفلتر).

---

## 2) ما الموجود داخل الموديول (Odoo)

**مكانه في الكود:** `msolutions_custom/ai_leadgen/` (إضافة عامة global، مثبّتة على `sameh` فقط). يعتمد على `crm` فقط.

**القوائم:** CRM ← **AI Lead Gen**
- **Operations**: لوحة الحالة (مصادر شغالة، الطابور، leads المنشورة، المشترين المؤهلين).
- **Sources**: صحة كل مصدر (يملؤها المحرك).
- **Candidates**: كل طلب فحصه المحرك وقراره (مقبول/مرفوض/السبب).
- **Deep Sync Run / Runs / Import groups from the engine / Authorizations**: مرتبطة بمحرك على **نفس السيرفر**، لذلك **لا تعمل حاليًا** (المحرك في حاوية منفصلة). الشغل الفعلي تلقائي من الحاوية، لا تحتاج الضغط عليها.
- **Target Groups**: الجروبات التي يقرأها المحرك. **هذه هي القائمة الوحيدة التي يعمل عليها المحرك** (`only_odoo_groups`). تقدر تضيف/توقف/ترتّب بالسحب والإفلات (الأعلى يُزار أكثر). الافتراضي 10 جروبات Odoo عامة.
- **Settings**: مزوّد الـ AI (Gemini المجاني أو DeepSeek المدفوع)، الحد الأدنى للتقييم، الـ proxy.

**الموديلات:** `ai.leadgen.run`, `ai.leadgen.run.line`, `ai.leadgen.session`, `leadgen.source` (Target Groups), `ai.leadgen.source.health`, `ai.leadgen.candidate`, `ai.leadgen.dashboard`.

**حقول جديدة على `crm.lead`:** `ai_score` (1-10)، `ai_whatsapp_pitch`، `ai_intent_category`، `ai_intent_level`، `ai_contact_status`، `ai_source_published`. وفلاتر بحث: "AI Lead Gen"، "From Facebook"، "No phone"، "Facebook, no phone". وعلى الـ lead: وسوم (`AI_Lead` وغيرها)، تحليل بالعربية، رسالة واتساب جاهزة، سكريبت مكالمة 30 ثانية، رابط البوست وملف الكاتب.

**الـ crons داخل Odoo** (تعمل في حاوية `odoo_worker`): تحديث الـ runs (2 دقيقة)، مزامنة الجروبات (15 دقيقة)، مزامنة لوحة العمليات (5 دقائق)، تشغيل مجدول (**معطّل**).
> بعد أي تثبيت/ترقية لازم إعادة تشغيل `odoo_worker` وإلا لا تعمل الـ crons الجديدة (workflow التثبيت يفعل ذلك).

**كيف تصل بيانات المحرك للوحة العمليات؟** المحرك يستدعي دالة عامة في الموديول: `ai.leadgen.candidate.remote_push_snapshot` (للمدير System فقط). لو لم يصل تقرير من المحرك لمدة **15 دقيقة** تعرض اللوحة: "No lead-generation engine… none has reported in the last 15 minutes" — وهذه أول علامة أن الحاوية واقفة.

---

## 3) المحرك (engine)

**الكود:** ريبو GitHub **خاص (private)**: `SamehAbdelAlYoussef/odoo-ai-leadgen-engine`. (لا يوجد منه نسخة في هذا الريبو العام.)

**المصادر الحالية (فقط):**
1. **جروبات فيسبوك** من قائمة Target Groups فقط. تُقبل leads بدون رقم هاتف. لا يوجد أي فلتر ميزانية. **أي منشور إعلاني من شركة (Vendor advertising) يُرفض** (قاعدة أضيفت 2026-11-19 بعد أن مرّ إعلان كطلب).
2. **تليجرام:** البوت `@ArabicLeadHunterBot`. يقرأ فقط الجروبات التي **أضاف فيها أصحابها البوت وصرّحوا** + الطلبات المباشرة للبوت. حاليًا **لا يوجد جروب مصرّح به**.
3. **الخرائط:** OpenStreetMap (Google Places معطّل لأنه يحتاج حساب فوترة).

**مسار كل طلب:** جمع ← فلتر الدولة (تليجرام/خرائط) ← حكم Gemini (تقييم 1-10، نية الشراء، التصنيف) ← إثراء بيانات التواصل ← بوابة القبول (للمجتمعات: تقييم ≥ 4 ولو بدون تواصل) ← منع التكرار ← فتح lead في Odoo.

**طبقة الأمان لفيسبوك (لا تُلغَ):** زيارة جروب واحد كل 15-20 دقيقة، ساعات هدوء **01:00-07:00 بتوقيت القاهرة**، حد يومي 60 زيارة، عمق 10 منشورات، وإذا أظهر فيسبوك تحذيرًا/checkpoint **يتوقف المحرك تلقائيًا 12-24 ساعة** (وهذا سلوك صحيح وليس عطلًا). الجروبات مرتبة بالأولوية (الأعلى تُزار أكثر).

**الـ AI:** Gemini (مجاني): النموذج الأساسي `gemini-3.5-flash-lite` (حد 500 طلب/يوم)، الاحتياطي `gemini-2.5-flash` (20/يوم)، وحد المحرك الذاتي 450/يوم. DeepSeek (مدفوع بالـ token) **لا يُستخدم إلا إذا اخترته** في Settings، والمفتاح غير موجود على السيرفر حاليًا.

**كلمات "التحقق":** الإيميل = صيغة + دومين + MX فقط (لا نقول إن الصندوق موجود). الهاتف = رقم صالح فقط، لا نقول إنه على واتساب.

---

## 4) السيرفر: أين يوجد كل شيء

| الشيء | المكان |
|---|---|
| حاويات Odoo | `odoo_web`, `odoo_gevent`, `odoo_trial`, `odoo_worker` (صورة `odoo:19` الأصلية، لا تُعدَّل) |
| حاوية المحرك | `leadgen_engine` (معالج واحد، ذاكرة 1.5 GB، `--cap-drop ALL`، `restart unless-stopped`) |
| مجلد المحرك | `/opt/leadgen/` : `src/` (الكود)، `data/` (قاعدة SQLite + الجلسات)، `logs/`، `engine.env` (الإعدادات والأسرار، صلاحية 600) |
| **جلسة فيسبوك** | `/opt/leadgen/data/sessions/facebook.json` + `facebook.meta.json` |
| قاعدة بيانات Odoo | `sameh` على PostgreSQL الخارجي |
| كود الموديول على السيرفر | `/opt/odoo/msolutions_custom/ai_leadgen` (يُسحب بـ `git pull` عند كل push على `main`) |

**حلقة العمل داخل الحاوية (`docker_loop.sh`):**
- **كل 5 دقائق:** `pull-groups` (يسحب Target Groups من Odoo) ← `poll` (زيارة فيسبوك لو وُجدت الجلسة؛ الضوابط تقرر هل حان الوقت) ← `push-ops` (يبعث تقرير اللوحة).
- **كل ساعة:** `run -p telegram -p maps`.
- `poll` و`run` يتشاركان قفلًا واحدًا: لو تصادما تطبع "lock held" وتتخطى — **طبيعي وليس خطأ**.

**الأسرار (GitHub Actions secrets في ريبو `msolutions_custom`، بالأسماء فقط):**
`SSH_HOST`, `SSH_PORT`, `SSH_USER`, `SSH_PRIVATE_KEY` (دخول CI للسيرفر)، `CUSTOM_PATH`, `ODOO_SERVICE`، `LEADGEN_DEPLOY_KEY` (قراءة فقط لريبو المحرك الخاص)، `LEADGEN_GEMINI_API_KEY`, `LEADGEN_TELEGRAM_BOT_TOKEN`، `LEADGEN_FB_SESSION_B64` (**نسخة من جلسة فيسبوك**).

**مفتاح API بين المحرك و Odoo:** يُولَّد **على السيرفر** (للمستخدم الإداري uid 2 في قاعدة `sameh`) ويُكتب مباشرة في `engine.env`، ولا يظهر في أي log. لإلغائه: Odoo ← Preferences ← Account Security ← احذف مفتاح `leadgen-engine`.

---

## 5) كل أوامر التشغيل (GitHub ← Actions ← اختر الـ workflow ← Run workflow)

لا حاجة لدخول السيرفر ولا لأي كلمة سر. **لا تستعمل كلمة سر السيرفر أبدًا لهذا الغرض.**

| Workflow | الاستخدام |
|---|---|
| **Run a fixed check inside the AI Lead Gen engine container** | اختر أمرًا: `logs` (آخر السجل)، `stats` (إحصاء 7 أيام + استهلاك Gemini)، `check-odoo` (يجرّب إنشاء lead تجريبية ويحذفها)، `preflight` (فحص شامل)، `check-ai`، `telegram-status`، `run-now` (دورة تليجرام+خرائط الآن)، **`stop-engine` (إيقاف طارئ)**، `start-engine`، `restart-engine` |
| **Deploy the AI Lead Gen engine** | بناء الصورة وتشغيل الحاوية من ريبو المحرك. خياران: `replace_fb_session` (استبدال جلسة فيسبوك)، `regenerate_api_key` (مفتاح Odoo جديد) |
| **Install a module on ONE tenant** | تثبيت/ترقية `ai_leadgen` على قاعدة `sameh` (تحديد `upgrade` بعد تغيير الكود). يمنح دور الويب صلاحية الجداول، ويعيد تشغيل `odoo_worker`. يرفض قاعدة التحكم `msolutions` |
| **Deploy to Server** | تلقائي عند كل push على `main`: `git pull` + إعادة تشغيل **Odoo لكل العملاء** |

> **تنبيه:** كل push على `main` يعيد تشغيل Odoo لكل العملاء. اجمع التعديلات في push واحد. شغّل أي workflow بعد الـ push بحوالي 20-30 ثانية (انتظر عودة `odoo_worker`).

---

## 6) جلسة فيسبوك: أين تُحفظ وكيف تُجدَّد (المهم لو حصل عطل)

**أين هي؟**
- **على السيرفر (المستخدمة فعليًا):** `/opt/leadgen/data/sessions/facebook.json` و`facebook.meta.json`.
- **على جهازك:** `odoo_ai_leadgen/data/sessions/` (نفس الاسمين).
- **نسخة في GitHub:** secret باسم `LEADGEN_FB_SESSION_B64` (tar.gz ثم base64). **هذه الجلسة تعادل تسجيل دخول لحسابك، فعامِلها كسر.**
- الجلسة **لا تُكتب فوق** تلقائيًا؛ النشر العادي يتركها كما هي.

**متى تحتاج تجديدًا؟** إذا ظهر المصدر "Paused" في Operations مع رسالة جلسة/تحقق، أو انتهت صلاحية الجلسة (عادة بعد أسابيع)، أو غيّرت باسورد فيسبوك. (لو الإيقاف 12-24 ساعة بسبب تحذير فيسبوك فالمحرك يعود وحده؛ لا تجدد إلا إذا استمر.)

**خطوات التجديد (تحتاج جهازك مرة واحدة):**
1. على جهازك، في مجلد المحرك المحلي (`odoo_ai_leadgen`):
   ```bash
   .venv/bin/python main.py login --platform facebook
   ```
   تفتح نافذة متصفح؛ **سجّل دخولك بنفسك** (التحقق والـ 2FA بيدك). الملفان الجديدان يُحفظان في `data/sessions/`.
2. ارفعهما كـ secret بدون طباعة القيمة:
   ```bash
   cd data/sessions && tar -czf - facebook.json facebook.meta.json | base64 -w0 \
     | gh secret set LEADGEN_FB_SESSION_B64 -R SamehAbdelAlYoussef/msolutions_custom
   ```
3. GitHub ← Actions ← **Deploy the AI Lead Gen engine** ← Run workflow ← فعّل **`replace_fb_session`**.
4. بعد دقيقتين: `engine-command` ← `logs`. المطلوب أن ترى زيارة جروب ناجحة (Sources: community ok).

**ملاحظة أمان:** جلسة منسوخة لـ IP سيرفر قد يطلب فيسبوك تحققًا إضافيًا. لو حصل checkpoint أوقف المحرك فورًا (`stop-engine`) وادخل على فيسبوك يدويًا من جهازك أولًا. **لا تشغّل المحرك على جهازك في نفس الوقت** (تعارض جلسات).

---

## 7) المراقبة اليومية

- **أسهل طريقة:** CRM ← AI Lead Gen ← **Operations**. تحقق أن "Last synchronization" حديث (أقل من 15 دقيقة) وأن عدّاد "CRM leads created" يزيد.
- **الـ leads:** CRM ← Pipeline (أزل فلتر "My Pipeline")، أو فلتر **AI Lead Gen** / **From Facebook**.
- **السجل:** workflow `engine-command` ← `logs`. **الإحصاء:** `stats`.
- **الـ crons داخل Odoo:** Settings ← Technical ← Scheduled Actions (وضع debug) ← ابحث "AI Lead Gen"؛ "Next Execution Date" يجب أن يتحرك.
- **الإنتاجية المتوقعة:** قليلة. فيسبوك محدود الزيارات بالتصميم، وأغلب المنشورات ليست طلبات (إعلانات، توظيف، نقاش). الأولى كانت 10 منشورات ← 1-2 lead.

---

## 8) أعطال معروفة وحلولها

| العرض | السبب | الحل |
|---|---|---|
| `permission denied for table ai_leadgen_...` | الجداول التي ينشئها `odoo_worker` مملوكة لدور `odoo_provision`، بينما الويب يتصل بدور `odoo_web` | شغّل workflow **Install a module on ONE tenant** (فيه خطوة GRANT). مطلوب بعد أي ترقية تضيف جداول |
| Operations تقول "No engine… none has reported in 15 min" | الحاوية واقفة أو لا تصل لـ Odoo | `engine-command` ← `logs` ثم `restart-engine`. لو الحاوية غير موجودة: Deploy the engine |
| "lock held … poll failed: 3" في السجل | تصادم بين الدورة الساعية وفحص فيسبوك | طبيعي. الفحص التالي ينجح |
| "idle: next group visit allowed in N min" | الضوابط الأمنية تنتظر موعد الزيارة | طبيعي. لا تعدّل |
| المصدر "Paused" | فيسبوك أظهر تحذيرًا؛ المحرك يتوقف 12-24 ساعة حماية للحساب | انتظر. لو جلسة منتهية فجدّدها (القسم 6) |
| لا leads إطلاقًا | Gemini استُهلك، أو الجروبات كلها موقوفة، أو ساعات الهدوء، أو لا منشورات جديدة | `stats` (استهلاك Gemini)، راجع Target Groups (Active + Verified) |
| "401/Access Denied" من Odoo في سجل المحرك | مفتاح API انتهى أو أُلغي | Deploy the engine مع `regenerate_api_key` |
| Gemini 429 | حد المجاني اليومي | المحرك يتوقف ويحتفظ بالمرشحين ويكمل غدًا تلقائيًا |
| زر Run now / Deep Sync / Authorize يعطي خطأ | مرتبطة بمحرك على نفس السيرفر | تجاهلها؛ التشغيل تلقائي من الحاوية |
| فشل `-u ai_leadgen` أو الـ workflow بعد الـ push مباشرة | `odoo_worker` ما زال يعيد التشغيل | أعد التشغيل بعد 20-30 ثانية |

---

## 9) ما لا يعمل / قيود معروفة (بصراحة)

- تليجرام: لا جروبات مصرّحة بعد. تسجيل جروب يحتاج أمر `telegram-register` بمعاملات، **ولا يوجد workflow له بعد**.
- أزرار Run now / Deep Sync Run / Authorize / Import groups في الموديول لا تعمل مع محرك في حاوية منفصلة (تحتاج وضع "remote" لم يُبْنَ).
- مفتاح Gemini خارج Odoo: يُقرأ من ملف الإعدادات على السيرفر، وليس من شاشة Settings في الموديول.
- الموديول لم يُختبر على صورة `odoo:19` Community محليًا بعد (اختُبر 62/62 على الجهاز المحلي ثم ثُبّت وتمت المعاينة على `sameh`).
- دقة الـ AI: تصنيف الإعلانات/الطلبات ليس معصومًا؛ راجع الـ leads الأولى بنفسك.

---

## 10) إيقاف/إزالة

- **إيقاف المحرك فورًا:** `engine-command` ← `stop-engine` (لن يعود وحده؛ `start-engine` لإعادته).
- **إيقاف زيارات فيسبوك فقط:** أوقف كل الجروبات في Target Groups (Active = off) خلال 5 دقائق.
- **إزالة الموديول من `sameh`:** من shell على السيرفر `button_immediate_uninstall` للموديول (راجع `SETUP_TENANT.md`)، ثم ألغِ مفتاح API وأوقف الحاوية.

---

## 11) أمان — بالأسماء فقط

- غيّر **كلمة سر السيرفر** و**مفتاح DeepSeek** (تم تداولهما في محادثة).
- ملف `ms` الموجود في هذا الريبو العام يكشف عناوين IP للسيرفر: احذفه من الريبو وأغلق منفذ قاعدة البيانات 5432 إلا على عنوان الويب.
- أي push على هذا الريبو العام: لا تضع فيه أسرارًا أبدًا. الأسرار فقط في GitHub Secrets أو `engine.env` على السيرفر.
- نسخة جلسة فيسبوك في GitHub Secrets: استبدلها عند تغيير الباسورد، واحذف الـ secret لو لم تعد تحتاجه.

---

## 12) مراجع

- `SETUP_TENANT.md` (خطوات التثبيت على tenant).
- ريبو المحرك الخاص: `README.md`, `deploy/PRODUCTION.md` (نسخة systemd القديمة؛ **غير مستخدمة** على هذا السيرفر).
- المحرك محليًا (للتطوير فقط): `odoo_ai_leadgen/` على جهاز التطوير، وقاعدة اختبار `leadgen_test`.
