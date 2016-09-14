from whoosh.highlight import Fragmenter, Fragment, Formatter


class ParagraphFragmenter(Fragmenter):
    def __init__(self):
        pass

    def fragment_tokens(self, text, tokens):
        paragraph_tokens = []
        last_s = None
        last_e = None

        for t in tokens:
            if t.matched:
                s, e = self.get_paragraph_pos(text, t)
        #
                if paragraph_tokens and s != last_s:
                    yield Fragment(text, paragraph_tokens, last_s, last_e)
                    paragraph_tokens = []
                else:
                    paragraph_tokens.append(t.copy())
                last_s = s
                last_e = e

        if paragraph_tokens:
            yield Fragment(text, paragraph_tokens, last_s, last_e)

    def get_paragraph_pos(self, text, t):
        try:
            paragraph_start = text[:t.startchar].rindex('\n')
        except ValueError:
            paragraph_start = 0
        try:
            paragraph_end = t.endchar + text[t.endchar:].index('\n')
        except ValueError:
            paragraph_end = len(text)
        return paragraph_start, paragraph_end


class TokenPosFormatter(Formatter):
    def __init__(self):
        self.between = '\n'

    def _text(self, text):
        return ""

    def format_token(self, text, token, replace=False):
        if token.matched:
            return str(token.startchar) + " "
        return ""