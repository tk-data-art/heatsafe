"""
services/ai_advisory.py

Multilingual Tactical Safety Briefing generator powered by Amazon Bedrock
(Anthropic Claude 3 Haiku) with resilient in-memory caching and fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("heatsafe.ai_advisory")

# In-memory dictionary cache keyed by (band, role, language) -> list of 3 bullet points
_ADVISORY_CACHE: dict[tuple[str, str, str], list[str]] = {}

DEFAULT_BEDROCK_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"
AWS_REGION = "us-east-1"

_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ar": "Arabic",
    "hi": "Hindi",
    "es": "Spanish",
}

# ---------------------------------------------------------------------------
# Pre-compiled static bullet points for resilient fallback
# ---------------------------------------------------------------------------

_FALLBACK_ADVISORIES: dict[tuple[str, str, str], list[str]] = {
    # English (en)
    ("Normal", "outdoor_worker", "en"): [
        "Drink water regularly throughout your work shift.",
        "Wear lightweight, breathable clothing and sun protection.",
        "Report any dizziness or fatigue to your supervisor immediately.",
    ],
    ("Normal", "general_public", "en"): [
        "Stay hydrated by drinking water throughout the day.",
        "Wear light clothing and apply sunscreen when outside.",
        "Enjoy outdoor activities while observing general heat safety.",
    ],
    ("Caution", "outdoor_worker", "en"): [
        "Drink at least half a liter of water every hour.",
        "Take short breaks in the shade every hour.",
        "Watch your coworkers for early signs of heat illness.",
    ],
    ("Caution", "general_public", "en"): [
        "Drink plenty of water and avoid sugary drinks.",
        "Wear light-colored clothing and stay in the shade.",
        "Limit strenuous exercise during peak afternoon heat.",
    ],
    ("Extreme Caution", "outdoor_worker", "en"): [
        "Drink water every 20 minutes even if you do not feel thirsty.",
        "Take a 20-minute rest break in shaded or air-conditioned areas.",
        "Wear lightweight, loose-fitting, light-colored clothing.",
    ],
    ("Extreme Caution", "general_public", "en"): [
        "Drink water every 20 minutes even if you do not feel thirsty.",
        "Take a 20-minute rest break in shaded or air-conditioned areas.",
        "Wear lightweight, loose-fitting, light-colored clothing.",
    ],
    ("Danger", "outdoor_worker", "en"): [
        "Drink one liter of cool electrolyte water every hour.",
        "Take a mandatory 40-minute shaded rest break every hour.",
        "Stop work immediately if you feel dizzy, nauseous, or stop sweating.",
    ],
    ("Danger", "general_public", "en"): [
        "Drink one liter of cool electrolyte water every hour.",
        "Take a mandatory 40-minute shaded rest break every hour.",
        "Stop work immediately if you feel dizzy, nauseous, or stop sweating.",
    ],
    ("Extreme Danger", "outdoor_worker", "en"): [
        "Halt all outdoor work immediately to prevent fatal heat stroke.",
        "Move all personnel inside climate-controlled emergency shelters.",
        "Call emergency medical services immediately for any heat symptoms.",
    ],
    ("Extreme Danger", "general_public", "en"): [
        "Stay indoors with air conditioning and keep window blinds closed.",
        "Do not rely on fans alone when indoor heat exceeds thirty-five degrees.",
        "Call emergency services immediately if anyone experiences high fever or confusion.",
    ],

    # Arabic (ar)
    ("Normal", "outdoor_worker", "ar"): [
        "حافظ على شرب الماء بانتظام طوال فترة العمل.",
        "ارتدِ ملابس عمل خفيفة وفضفاضة مع غطاء مناسب للرأس.",
        "أبلغ المشرف فوراً عن أي شعور بالإجهاد أو الدوار.",
    ],
    ("Normal", "general_public", "ar"): [
        "حافظ على الترطيب الجيد بشرب كميات كافية من الماء يومياً.",
        "ارتدِ ملابس صيفية خفيفة واستخدم واقي الشمس عند الخروج.",
        "مارس أنشطتك اليومية بشكل طبيعي مع مراعاة شرب السوائل.",
    ],
    ("Caution", "outdoor_worker", "ar"): [
        "اشرب ما لا يقل عن نصف لتر من الماء كل ساعة حتى بدون عطش.",
        "خذ فترات راحة قصيرة في الظل أو في أماكن جيدة التهوية كل ساعة.",
        "راقب زملاء العمل بحثاً عن علامات الإجهاد الحراري المبكرة كالدوخة.",
    ],
    ("Caution", "general_public", "ar"): [
        "اشرب كميات كافية من الماء وتجنب المشروبات السكرية والمجففة للجسم.",
        "ارتدِ ملابس قطنية فاتحة اللون وقبعة عند التعرض المباشر للشمس.",
        "قلل من الأنشطة البدنية المجهدة في الهواء الطلق أثناء الظهيرة.",
    ],
    ("Extreme Caution", "outdoor_worker", "ar"): [
        "طبق جدول عمل يتضمن راحة إجبارية بنسبة 25٪ في الظل كل ساعة.",
        "اشرب 0.75 لتر من الماء في الساعة مع تناول أملاح ومعادن الترطيب.",
        "فعل نظام الزميل لمراقبة علامات الغثيان أو التشنج العضلي.",
    ],
    ("Extreme Caution", "general_public", "ar"): [
        "ابقَ في أماكن مكيفة ومظللة خلال ساعات الذروة والحرارة الشديدة.",
        "أجل ممارسة الرياضة في الهواء الطلق إلى الصباح الباكر أو المساء.",
        "احرص على متابعة كبار السن والأطفال لضمان حصولهم على السوائل الكافية.",
    ],
    ("Danger", "outdoor_worker", "ar"): [
        "أوقف الأعمال البدنية الشاقة غير الضرورية فوراً في الأماكن المكشوفة.",
        "التزم بنظام 50٪ عمل و50٪ راحة تحت أجهزة التبريد والرذاذ المائي.",
        "جهز نقاط الطوارئ بالماء البارد والثلج لتقديم الإسعافات الفورية.",
    ],
    ("Danger", "general_public", "ar"): [
        "تجنب الخروج تحت أشعة الشمس تماماً والزم الأماكن المكيفة.",
        "اشرب الماء البارد بشكل متكرر وراقب مؤشرات الإعياء الحراري.",
        "اطلب الرعاية الطبية الطارئة فوراً عند الشعور بالدوار أو الارتباك.",
    ],
    ("Extreme Danger", "outdoor_worker", "ar"): [
        "أوقف جميع الأعمال الميدانية فوراً؛ خطر ضربة الشمس مهدد للحياة.",
        "انقل جميع العاملين إلى ملاجئ طوارئ مكيفة واعتمد التبريد المباشر.",
        "استدعِ الإسعاف فوراً لأي شخص تظهر عليه أعراض ارتفاع درجة الحرارة أو الإغماء.",
    ],
    ("Extreme Danger", "general_public", "ar"): [
        "الزم البقاء داخل المنزل في غرف مكيفة واغلق الستائر لمنع دخول الحرارة.",
        "لا تعتمد على المراوح فقط إذا تجاوزت درجة الحرارة 35 درجة مئوية.",
        "اتصل بالإسعاف فوراً إذا عانى أي شخص من فقدان الوعي أو الحمى الشديدة.",
    ],

    # Hindi (hi)
    ("Normal", "outdoor_worker", "hi"): [
        "काम की शिफ्ट के दौरान नियमित अंतराल पर पर्याप्त पानी पीते रहें।",
        "हल्के, हवादार कपड़े और धूप से बचाने वाली टोपी पहनें।",
        "कमजोरी या चक्कर आने पर तुरंत अपने सुपरवाइजर को सूचित करें।",
    ],
    ("Normal", "general_public", "hi"): [
        "दिनभर पर्याप्त मात्रा में पानी पीकर शरीर को हाइड्रेटेड रखें।",
        "धूप में बाहर निकलते समय सूती कपड़े और सनस्क्रीन का उपयोग करें।",
        "सामान्य दिनचर्या के दौरान पानी की बोतल हमेशा साथ रखें।",
    ],
    ("Caution", "outdoor_worker", "hi"): [
        "प्यास न लगने पर भी हर घंटे कम से कम आधा लीटर पानी पिएं।",
        "हर घंटे छायादार या हवादार जगह पर 10-15 मिनट का विश्राम लें।",
        "सिरदर्द या थकान जैसे हीट स्ट्रेस के शुरुआती लक्षणों पर नजर रखें।",
    ],
    ("Caution", "general_public", "hi"): [
        "भरपूर पानी पिएं और कैफीन या अधिक मीठे पेय पदार्थों से बचें।",
        "हल्के रंग के ढीले सूती कपड़े पहनें और सिर को ढककर रखें।",
        "दोपहर के समय तेज धूप में भारी शारीरिक गतिविधियों से बचें।",
    ],
    ("Extreme Caution", "outdoor_worker", "hi"): [
        "छाया में 25% आराम और 75% काम का अनिवार्य रोटेशन लागू करें।",
        "प्रति घंटे 0.75 लीटर पानी पिएं और ओआरएस/इलेक्ट्रोलाइट का सेवन करें।",
        "साथी प्रणाली अपनाएं ताकि मांसपेशियों में ऐंठन या चक्कर आने पर मदद मिल सके।",
    ],
    ("Extreme Caution", "general_public", "hi"): [
        "दोपहर के चरम समय में वातानुकूलित या ठंडी जगहों पर ही रहें।",
        "व्यायाम या भारी काम सुबह जल्दी या देर शाम को ही करें।",
        "बच्चों और बुजुर्गों के हाइड्रेशन का विशेष ध्यान रखें।",
    ],
    ("Danger", "outdoor_worker", "hi"): [
        "बाहर सभी गैर-जरूरी भारी शारीरिक कार्यों को तुरंत रोक दें।",
        "50% काम और 50% आराम का चक्र ठंडी छांव में लागू करें।",
        "प्राथमिक उपचार और बर्फ के पानी की तुरंत उपलब्धता सुनिश्चित करें।",
    ],
    ("Danger", "general_public", "hi"): [
        "तेज धूप में बाहर निकलने से पूरी तरह बचें और ठंडे कमरों में रहें।",
        "लगातार ठंडा पानी पिएं और तेज धड़कन या बेचैनी को नजरअंदाज न करें।",
        "चक्कर आने या बेहोशी महसूस होने पर तुरंत नजदीकी अस्पताल जाएं।",
    ],
    ("Extreme Danger", "outdoor_worker", "hi"): [
        "सभी बाहरी कार्य तुरंत बंद करें; हीट स्ट्रोक का जानलेवा खतरा है।",
        "सभी कर्मियों को वातानुकूलित सुरक्षित कमरों में ले जाएं और ठंडा करें।",
        "लक्षण दिखने पर बिना देरी किए आपातकालीन चिकित्सा सहायता बुलाएं।",
    ],
    ("Extreme Danger", "general_public", "hi"): [
        "पूरी तरह से घर के अंदर एसी वाले कमरे में रहें और खिड़कियां बंद रखें।",
        "कमरे का तापमान 35°C से अधिक होने पर केवल पंखे पर निर्भर न रहें।",
        "तेज बुखार या मानसिक भ्रम की स्थिति में तुरंत एम्बुलेंस को कॉल करें।",
    ],

    # Spanish (es)
    ("Normal", "outdoor_worker", "es"): [
        "Mantenga una hidratación regular bebiendo agua durante todo su turno.",
        "Use ropa ligera, transpirable y protección para la cabeza contra el sol.",
        "Comunique de inmediato a su supervisor cualquier síntoma de fatiga o mareo.",
    ],
    ("Normal", "general_public", "es"): [
        "Beba suficiente agua para mantenerse bien hidratado durante el día.",
        "Use ropa fresca, protector solar y sombrero al salir al exterior.",
        "Mantenga hábitos saludables de hidratación durante sus actividades diarias.",
    ],
    ("Caution", "outdoor_worker", "es"): [
        "Beba al menos 0.5 litros de agua fresca por hora, incluso sin sed.",
        "Tome descansos de 10 a 15 minutos en áreas con sombra y ventilación cada hora.",
        "Vigile a sus compañeros de trabajo ante señales tempranas de fatiga o dolor de cabeza.",
    ],
    ("Caution", "general_public", "es"): [
        "Aumente el consumo diario de agua y evite bebidas alcohólicas o con exceso de azúcar.",
        "Vista ropa ligera y de colores claros con protección solar adecuada.",
        "Reduzca el esfuerzo físico prolongado bajo el sol en las horas del mediodía.",
    ],
    ("Extreme Caution", "outdoor_worker", "es"): [
        "Aplique una rotación obligatoria de 25% de descanso y 75% de trabajo en la sombra.",
        "Consuma 0.75 litros de agua por hora junto con soluciones de electrolitos.",
        "Use el sistema de parejas para detectar a tiempo náuseas, calambres o mareos.",
    ],
    ("Extreme Caution", "general_public", "es"): [
        "Permanezca en interiores con aire acondicionado durante las horas de calor pico.",
        "Reprograme actividades deportivas intensas para la mañana temprano o la noche.",
        "Asegúrese de que niños y adultos mayores beban suficiente líquido regularmente.",
    ],
    ("Danger", "outdoor_worker", "es"): [
        "Suspenda de inmediato los trabajos físicos pesados no esenciales al aire libre.",
        "Establezca ciclos de 50% de trabajo y 50% de descanso en refugios climatizados.",
        "Mantenga listo equipo de primeros auxilios con agua fría y compresas de hielo.",
    ],
    ("Danger", "general_public", "es"): [
        "Evite salir al exterior y permanezca en ambientes frescos y refrigerados.",
        "Beba agua fresca de forma continua y vigile signos de golpe de calor.",
        "Acuda a urgencias médicas de inmediato si siente desorientación o desmayo.",
    ],
    ("Extreme Danger", "outdoor_worker", "es"): [
        "Detenga todas las actividades laborales exteriores; riesgo inminente de muerte.",
        "Evacúe al personal hacia refugios con aire acondicionado y aplique enfriamiento activo.",
        "Llame de inmediato a los servicios de emergencias médicas ante cualquier síntoma.",
    ],
    ("Extreme Danger", "general_public", "es"): [
        "Permanezca estrictamente en interiores con aire acondicionado y persianas cerradas.",
        "No confíe únicamente en ventiladores si la temperatura interior supera los 35°C.",
        "Llame a los servicios de emergencia de inmediato ante fiebre alta o confusión.",
    ],
}


def get_fallback_advisory(band: str, role: str, language: str = "en") -> list[str]:
    """Retrieve pre-compiled static bullet points for the given band, role, and language."""
    canonical_lang = language.lower().strip() if language else "en"
    if canonical_lang not in _LANGUAGE_NAMES:
        canonical_lang = "en"

    # Exact tuple check
    if (band, role, canonical_lang) in _FALLBACK_ADVISORIES:
        return list(_FALLBACK_ADVISORIES[(band, role, canonical_lang)])

    # Band/role fallback with current language
    for b in (band, "Caution", "Normal"):
        for r in (role, "general_public", "outdoor_worker"):
            if (b, r, canonical_lang) in _FALLBACK_ADVISORIES:
                return list(_FALLBACK_ADVISORIES[(b, r, canonical_lang)])

    # Absolute fallback in English
    return list(_FALLBACK_ADVISORIES[("Caution", "general_public", "en")])


def _parse_bullet_points(text: str) -> list[str]:
    """Extract bullet points from model text response."""
    bullets: list[str] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^(?:[-*•]|\d+[\.)])\s*", "", line).strip()
        if cleaned:
            bullets.append(cleaned)
    return bullets


def _invoke_bedrock_sync(band: str, role: str, language: str) -> list[str]:
    """Synchronous invocation of Amazon Bedrock Claude 3 Haiku."""
    lang_name = _LANGUAGE_NAMES.get(language, "English")
    persona_desc = "Outdoor Worker" if role == "outdoor_worker" else "General Public"

    prompt = (
        f"You are an expert occupational safety and heat-health advisor generating a spoken audio briefing.\n"
        f"Generate exactly 3 concise, imperative safety precautions tailored to these conditions:\n"
        f"- Heat Stress Band: {band}\n"
        f"- User Persona: {persona_desc}\n"
        f"- Target Language: {lang_name}\n\n"
        f"Requirements for Spoken Clarity:\n"
        f"1. Provide exactly 3 bullet points, each starting with '- '.\n"
        f"2. Write in {lang_name}.\n"
        f"3. Use direct, imperative commands (e.g., 'Drink water every 15 minutes', 'Rest in shade now').\n"
        f"4. Keep each sentence punchy, natural, and under 15 words for crystal-clear text-to-speech enunciation.\n"
        f"5. Avoid passive voice, complex subordinate clauses, acronyms, and parenthetical phrases.\n"
        f"6. Do NOT include any introductory or concluding text, titles, or headers."
    )

    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 350,
        "temperature": 0.2,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    response = client.invoke_model(
        modelId=DEFAULT_BEDROCK_MODEL_ID,
        body=json.dumps(payload),
        contentType="application/json",
        accept="application/json",
    )

    body_bytes = response["body"].read() if hasattr(response["body"], "read") else response["body"]
    if isinstance(body_bytes, bytes):
        body_str = body_bytes.decode("utf-8")
    else:
        body_str = str(body_bytes)

    data = json.loads(body_str)
    raw_text = data.get("content", [{}])[0].get("text", "")
    bullets = _parse_bullet_points(raw_text)
    if len(bullets) >= 3:
        return bullets[:3]
    raise ValueError(f"Bedrock returned fewer than 3 bullet points ({len(bullets)} found)")


async def get_safety_briefing(band: str, role: str, language: str = "en") -> list[str]:
    """
    Return 3 short, actionable, bullet-pointed occupational safety precautions
    tailored to the heat band, user role, and target language.

    Checks in-memory cache first, invokes Amazon Bedrock if uncached, and falls
    back to pre-compiled static bullet points if Bedrock is unavailable.
    """
    canonical_lang = language.lower().strip() if language else "en"
    if canonical_lang not in _LANGUAGE_NAMES:
        canonical_lang = "en"

    cache_key = (band, role, canonical_lang)

    # 1. In-memory cache check
    if cache_key in _ADVISORY_CACHE:
        return _ADVISORY_CACHE[cache_key]

    # 2. Bedrock invocation with resilient fallback
    try:
        bullets = await asyncio.to_thread(_invoke_bedrock_sync, band, role, canonical_lang)
    except Exception as exc:
        logger.warning(
            "Amazon Bedrock call failed for (%s, %s, %s): %s; using static fallback",
            band,
            role,
            canonical_lang,
            exc,
        )
        bullets = get_fallback_advisory(band, role, canonical_lang)

    # 3. Cache and return
    _ADVISORY_CACHE[cache_key] = bullets
    return bullets
