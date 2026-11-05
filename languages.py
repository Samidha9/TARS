"""The languages TARS supports: the 25 that Muse Voice Transcribe officially supports (dev.meta.ai/docs/speech-to-text).
Names are what the listener expects; codes are the language part of a BCP-47 tag."""

SUPPORTED = {
    "Arabic": ["ar"], "Bengali": ["bn"], "Dutch": ["nl"], "English": ["en"], "French": ["fr"],
    "German": ["de"], "Hebrew": ["he", "iw"], "Hindi": ["hi"], "Indonesian": ["id", "in"], "Italian": ["it"],
    "Japanese": ["ja"], "Kannada": ["kn"], "Korean": ["ko"], "Malay": ["ms"], "Mandarin Chinese": ["zh", "cmn"],
    "Marathi": ["mr"], "Polish": ["pl"], "Portuguese": ["pt"], "Spanish": ["es"], "Tagalog": ["tl", "fil"],
    "Tamil": ["ta"], "Telugu": ["te"], "Thai": ["th"], "Turkish": ["tr"], "Vietnamese": ["vi"],
}
CODES = {code for codes in SUPPORTED.values() for code in codes}


def supported(lang):
    """True if a BCP-47 tag is one of the supported languages."""
    return lang.split("-")[0].lower() in CODES

