import re
import random

# Pattern to find innermost spintax block: {a|b|c}
# This regex matches the innermost braces that do not contain other braces
SPINTAX_PATTERN = re.compile(r'\{([^{}]+)\}')

def evaluate_spintax(text: str) -> str:
    """
    Evaluates spintax randomly. Supports nested spintax.
    Example: "{Hello|Hi}, how are {you|things}?" -> "Hi, how are things?"
    """
    if not text:
        return text

    def replacer(match):
        options = match.group(1).split('|')
        return random.choice(options)

    # Keep replacing innermost spintax until none are left
    while SPINTAX_PATTERN.search(text):
        text = SPINTAX_PATTERN.sub(replacer, text)
        
    return text
