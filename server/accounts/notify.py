"""Emails the app sends on its own (account codes and password notices stay in auth.py).

  welcome          a new account is confirmed (email code, Google or GitHub)
  run_done         the agents finished (or stopped) a long run while nobody had the studio open
  joined           someone you invited to a project created their account and joined it
  plan_active      a paid plan turned on (PayPal confirmed, or an admin approved the request)
  plan_ended       a PayPal subscription was cancelled, suspended or expired: back to Starter
  payment_failed   PayPal couldn't take a payment
  renewal          a yearly subscription renews in RENEWAL_NOTICE_DAYS days (sent once per renewal)

Like auth.py: nothing a person typed goes in an email (no project names, no comments), only links to the app when
PUBLIC_URL is set. Every email is sent in the background (auth.send_later) so it never slows down or fails a request.
Members can turn off the run emails on their profile (users.notify_runs); billing emails always go out.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from accounts import auth
from core import config
from core import db
from accounts import plans

log = logging.getLogger("agents-atelier.notify")

RENEWAL_NOTICE_DAYS = 7
CHECK_SECONDS = 3600          # how often the renewal reminders are looked for


def _link(path: str) -> str:
    return f"{config.PUBLIC_URL}{path}" if config.PUBLIC_URL else ""


EMAILS = {
    "welcome": {
        "en": ("Welcome to Agents Atelier",
               "Hello,\n\nYour account is ready. Describe the website you need and hire a team of AI agents to build it while you watch.\n\n"
               "Three tips to start:\n- Pick a ready team for your kind of site: it comes with a prompt to complete.\n"
               "- Add your Claude API key in your profile: the agents run on your own Claude account.\n"
               "- Share a link with your client: they can pin comments right on the page.\n{link}"),
        "fr": ("Bienvenue sur Agents Atelier",
               "Bonjour,\n\nVotre compte est prêt. Décrivez le site dont vous avez besoin et engagez une équipe d'agents IA qui le construit sous vos yeux.\n\n"
               "Trois conseils pour commencer :\n- Choisissez une équipe prête pour votre type de site : elle vient avec une demande à compléter.\n"
               "- Ajoutez votre clé API Claude dans votre profil : les agents travaillent sur votre propre compte Claude.\n"
               "- Partagez un lien avec votre client : il peut épingler des commentaires directement sur la page.\n{link}"),
        "ary": ("مرحبا بيك ف Agents Atelier",
                "السلام،\n\nالحساب ديالك واجد. وصف السيت اللي محتاج وخدم فريق ديال الوكلاء ديال الذكاء الاصطناعي يبنيوه قدام عينيك.\n\n"
                "تلاتة ديال النصائح باش تبدا:\n- ختار فريق واجد للنوع ديال السيت ديالك: كيجي معاه طلب تكملو.\n"
                "- زيد المفتاح ديال Claude API ف البروفيل ديالك: الوكلاء كيخدمو على الحساب ديالك ف Claude.\n"
                "- بارطاجي رابط مع الكليان ديالك: يقدر يحط تعليقات نيشان على الصفحة.\n{link}"),
    },
    "run_done": {
        "en": ("Your agents finished their work",
               "Hello,\n\nThe agents finished the request you sent ({minutes}). Open the studio to see the result, ask for changes or share a link.\n{link}"),
        "fr": ("Vos agents ont terminé leur travail",
               "Bonjour,\n\nLes agents ont terminé la demande que vous avez envoyée ({minutes}). Ouvrez le studio pour voir le résultat, demander des changements ou partager un lien.\n{link}"),
        "ary": ("الوكلاء ديالك ساليو الخدمة",
                "السلام،\n\nالوكلاء ساليو الطلب اللي صيفطتي ({minutes}). حل الستوديو باش تشوف النتيجة، تطلب تبديلات ولا تبارطاجي رابط.\n{link}"),
    },
    "run_stopped": {
        "en": ("Your agents stopped before the end",
               "Hello,\n\nThe agents stopped before finishing your request ({minutes}): {why}\nWhat's done is kept. Open the studio and press Continue to go on.\n{link}"),
        "fr": ("Vos agents se sont arrêtés avant la fin",
               "Bonjour,\n\nLes agents se sont arrêtés avant de terminer votre demande ({minutes}) : {why}\nCe qui est fait est conservé. Ouvrez le studio et appuyez sur Continuer pour reprendre.\n{link}"),
        "ary": ("الوكلاء ديالك وقفو قبل ما يساليو",
                "السلام،\n\nالوكلاء وقفو قبل ما يساليو الطلب ديالك ({minutes}): {why}\nداكشي اللي تدار محفوظ. حل الستوديو وضغط على كمل باش يكملو.\n{link}"),
    },
    "joined": {
        "en": ("Someone you invited joined your project",
               "Hello,\n\n{email} created their account and joined one of your projects as {role}.\n\nYou can change their role or remove them in the project's Share tab.\n{link}"),
        "fr": ("Une personne invitée a rejoint votre projet",
               "Bonjour,\n\n{email} a créé son compte et a rejoint l'un de vos projets en tant que {role}.\n\nVous pouvez changer son rôle ou la retirer dans l'onglet Partager du projet.\n{link}"),
        "ary": ("شي واحد عرضتي عليه دخل للمشروع ديالك",
                "السلام،\n\n{email} دار الحساب ديالو ودخل لواحد من المشاريع ديالك ك{role}.\n\nتقدر تبدل الدور ديالو ولا تحيدو من خانة البارطاج ف المشروع.\n{link}"),
    },
    "plan_active": {
        "en": ("Your {plan} plan is active",
               "Hello,\n\nYour {plan} plan is active ({period}). Its features are on now: {features}.\n\nThank you for your trust.\n{link}"),
        "fr": ("Votre offre {plan} est active",
               "Bonjour,\n\nVotre offre {plan} est active ({period}). Ses fonctionnalités sont disponibles dès maintenant : {features}.\n\nMerci de votre confiance.\n{link}"),
        "ary": ("الباقة {plan} ديالك خدامة",
                "السلام،\n\nالباقة {plan} ديالك خدامة ({period}). المميزات ديالها متاحة دابا: {features}.\n\nشكرا على الثقة ديالك.\n{link}"),
    },
    "plan_declined": {
        "en": ("About your plan request",
               "Hello,\n\nAn admin looked at your request to change plan and didn't approve it this time. Your plan stays as it was.\n\n"
               "If you have questions, reply to the administrator of this service.\n{link}"),
        "fr": ("À propos de votre demande d'offre",
               "Bonjour,\n\nUn admin a examiné votre demande de changement d'offre et ne l'a pas approuvée cette fois. Votre offre reste la même.\n\n"
               "Pour toute question, contactez l'administrateur de ce service.\n{link}"),
        "ary": ("على الطلب ديال الباقة ديالك",
                "السلام،\n\nأدمين شاف الطلب ديالك باش تبدل الباقة وما وافقش عليه هاد المرة. الباقة ديالك بقات كيف كانت.\n\n"
                "إلا عندك شي سؤال، تواصل مع الأدمين ديال هاد الخدمة.\n{link}"),
    },
    "plan_ended": {
        "en": ("Your {plan} subscription has ended",
               "Hello,\n\nYour {plan} subscription was {how} at PayPal, so your account is back on the free Starter plan. Your projects are kept.\n\n"
               "You can subscribe again from your profile at any time.\n{link}"),
        "fr": ("Votre abonnement {plan} est terminé",
               "Bonjour,\n\nVotre abonnement {plan} a été {how} chez PayPal : votre compte est revenu à l'offre gratuite Starter. Vos projets sont conservés.\n\n"
               "Vous pouvez vous réabonner depuis votre profil à tout moment.\n{link}"),
        "ary": ("الاشتراك {plan} ديالك سالا",
                "السلام،\n\nالاشتراك {plan} ديالك {how} ف PayPal، داكشي علاش الحساب ديالك رجع للباقة الفابور Starter. المشاريع ديالك محفوظين.\n\n"
                "تقدر تعاود تشترك من البروفيل ديالك فاش ما بغيتي.\n{link}"),
    },
    "payment_failed": {
        "en": ("We couldn't take your payment",
               "Hello,\n\nPayPal couldn't take the payment for your {plan} plan. Check your payment method in your PayPal account: "
               "PayPal tries again in a few days, and the subscription stops if it keeps failing.\n{link}"),
        "fr": ("Nous n'avons pas pu encaisser votre paiement",
               "Bonjour,\n\nPayPal n'a pas pu encaisser le paiement de votre offre {plan}. Vérifiez votre moyen de paiement dans votre compte PayPal : "
               "PayPal réessaie dans quelques jours, et l'abonnement s'arrête si cela échoue encore.\n{link}"),
        "ary": ("ما قدرناش ناخدو الخلاص ديالك",
                "السلام،\n\nPayPal ما قدرش ياخد الخلاص ديال الباقة {plan} ديالك. شوف وسيلة الخلاص ف الحساب ديالك ف PayPal: "
                "PayPal غادي يعاود من بعد شي أيام، والاشتراك كيوقف إلا بقا ما خدمش.\n{link}"),
    },
    "renewal": {
        "en": ("Your {plan} plan renews on {date}",
               "Hello,\n\nYour yearly {plan} plan renews on {date}: PayPal will charge ${price} for the next year.\n\n"
               "Nothing to do if you want to keep it. To stop it, move to Starter on your profile before that date.\n{link}"),
        "fr": ("Votre offre {plan} se renouvelle le {date}",
               "Bonjour,\n\nVotre offre annuelle {plan} se renouvelle le {date} : PayPal prélèvera {price} $ pour l'année suivante.\n\n"
               "Rien à faire si vous la gardez. Pour l'arrêter, passez à Starter dans votre profil avant cette date.\n{link}"),
        "ary": ("الباقة {plan} ديالك كتجدد نهار {date}",
                "السلام،\n\nالباقة السنوية {plan} ديالك كتجدد نهار {date}: PayPal غادي ياخد {price} دولار على العام الجاي.\n\n"
                "ما تدير والو إلا بغيتي تخليها. باش توقفها، رجع لـ Starter ف البروفيل ديالك قبل هاد التاريخ.\n{link}"),
    },
}
WORDS = {
    "en": {"monthly": "paid monthly", "yearly": "paid yearly", "editor": "an editor", "viewer": "a viewer",
           "CANCELLED": "cancelled", "SUSPENDED": "suspended", "EXPIRED": "ended", "open": "Open Agents Atelier: {url}",
           "min": "{n} min", "sec": "less than a minute"},
    "fr": {"monthly": "payée au mois", "yearly": "payée à l'année", "editor": "éditeur", "viewer": "lecteur",
           "CANCELLED": "annulé", "SUSPENDED": "suspendu", "EXPIRED": "arrivé à son terme", "open": "Ouvrir Agents Atelier : {url}",
           "min": "{n} min", "sec": "moins d'une minute"},
    "ary": {"monthly": "بالشهر", "yearly": "بالعام", "editor": "محرر", "viewer": "متفرج",
            "CANCELLED": "تلغى", "SUSPENDED": "توقف", "EXPIRED": "سالا", "open": "حل Agents Atelier: {url}",
            "min": "{n} دقيقة", "sec": "أقل من دقيقة"},
}
FEATURES = {
    "pro": {"en": "unlimited projects, your own agents and teams, web research, more checks and versions",
            "fr": "projets illimités, vos propres agents et équipes, recherche web, plus de vérifications et de versions",
            "ary": "مشاريع بلا حدود، الوكلاء والفرق ديالك، البحث فالويب، فحوصات ونسخ كثر"},
    "agency": {"en": "everything in Pro, editors and viewers on your projects, publishing to the marketplace, every version kept",
               "fr": "tout Pro, des éditeurs et lecteurs sur vos projets, la publication sur la place de marché, toutes les versions gardées",
               "ary": "كلشي ديال Pro، محررين ومتفرجين ف المشاريع ديالك، النشر ف السوق، كاع النسخ محفوظين"},
}
PLAN_NAMES = {"ary": {"agency": "الوكالة"}, "fr": {"agency": "Agence"}}


def _lang(user) -> str:
    lang = (user or {}).get("language") or "en"
    return lang if lang in EMAILS["welcome"] else "en"


def _send(kind: str, user: dict, path: str = "/", **values):
    """Queue one email to this account, in its language."""
    if not user or not user.get("email"):
        return
    lang = _lang(user)
    words = WORDS[lang]
    url = _link(path)
    subject, text = EMAILS[kind][lang]
    values = {**values, "link": ("\n" + words["open"].format(url=url) + "\n") if url else ""}
    try:
        auth.send_later(user["email"], subject.format(**values), text.format(**values))
    except Exception:
        log.exception("Couldn't queue the %s email", kind)


def _plan_name(plan: str, lang: str) -> str:
    return PLAN_NAMES.get(lang, {}).get(plan) or plans.PLANS.get(plan, {}).get("name", plan)


async def _user(user_id) -> dict | None:
    try:
        row = await db.pool.fetchrow("SELECT id, email, language, plan, billing_period, notify_runs FROM users WHERE id = $1",
                                     uuid.UUID(str(user_id)))
    except (ValueError, TypeError):
        return None
    return dict(row) if row else None


# ------------------------------------------------------------ the events

async def welcome(user: dict):
    _send("welcome", await _user(user["id"]) or user, "/projects/new")


async def run_finished(pid: str, user_id, ok: bool, seconds: float, why: str = ""):
    """After a long run, when nobody has the studio open (main.py decides that)."""
    user = await _user(user_id)
    if not user or not user.get("notify_runs", True):
        return
    words = WORDS[_lang(user)]
    minutes = words["min"].format(n=max(1, round(seconds / 60))) if seconds >= 60 else words["sec"]
    if ok:
        _send("run_done", user, f"/p/{pid}", minutes=minutes)
    else:
        _send("run_stopped", user, f"/p/{pid}", minutes=minutes, why=why[:200] or "-")


async def joined(project_id: str, inviter_id, email: str, role: str):
    user = await _user(inviter_id)
    if user:
        _send("joined", user, f"/p/{project_id}?tab=share", email=email, role=WORDS[_lang(user)].get(role, role))


async def plan_active(user_id, plan: str, period: str = "monthly"):
    user = await _user(user_id)
    if not user or plan not in FEATURES:
        return
    lang = _lang(user)
    _send("plan_active", user, "/profile#plan", plan=_plan_name(plan, lang), period=WORDS[lang].get(period, period),
          features=FEATURES[plan][lang])


async def plan_declined(user_id):
    user = await _user(user_id)
    if user:
        _send("plan_declined", user, "/profile#plan")


async def plan_ended(user_id, plan: str, status: str):
    user = await _user(user_id)
    if user and plan in plans.PLANS:
        lang = _lang(user)
        _send("plan_ended", user, "/profile#plan", plan=_plan_name(plan, lang), how=WORDS[lang].get(status, WORDS[lang]["EXPIRED"]))


async def payment_failed(user_id, plan: str):
    user = await _user(user_id)
    if user:
        _send("payment_failed", user, "/profile#plan", plan=_plan_name(plan, _lang(user)))


async def renewal_reminders() -> int:
    """Yearly subscriptions renewing within RENEWAL_NOTICE_DAYS: one reminder per renewal date."""
    now = time.time()
    rows = await db.pool.fetch("""SELECT id, user_id, plan, next_billing FROM billing_subscriptions
                                  WHERE status = 'ACTIVE' AND period = 'yearly' AND next_billing BETWEEN $1 AND $2
                                    AND reminded_for IS DISTINCT FROM next_billing""", now, now + RENEWAL_NOTICE_DAYS * 86400)
    for r in rows:
        user = await _user(r["user_id"])
        if user:
            lang = _lang(user)
            date = datetime.fromtimestamp(r["next_billing"], timezone.utc).strftime("%Y-%m-%d")
            _send("renewal", user, "/profile#plan", plan=_plan_name(r["plan"], lang), date=date,
                  price=plans.PLANS.get(r["plan"], {}).get("price_yearly", ""))
        await db.pool.execute("UPDATE billing_subscriptions SET reminded_for = $2 WHERE id = $1", r["id"], r["next_billing"])
    return len(rows)


async def loop():
    """Every CHECK_SECONDS: the renewal reminders."""
    while True:
        try:
            await renewal_reminders()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Renewal reminders failed")
        await asyncio.sleep(CHECK_SECONDS)
