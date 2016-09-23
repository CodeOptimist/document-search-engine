from whoosh.highlight import Fragmenter, Fragment, Formatter, BasicFragmentScorer
import random


class ParagraphFragmenter(Fragmenter):
    def __init__(self):
        pass

    def fragment_tokens(self, text, tokens):
        paragraph_tokens = []
        last = (None, None)

        for t in tokens:
            if t.matched:
                cur = self.get_paragraph_pos(text, t)
        #
                if cur != last and paragraph_tokens:
                    yield Fragment(text, paragraph_tokens, last[0], last[1])
                    paragraph_tokens = []
                paragraph_tokens.append(t.copy())
                last = cur

        if paragraph_tokens:
            yield Fragment(text, paragraph_tokens, last[0], last[1])

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


class ConsistentFragmentScorer(BasicFragmentScorer):
    def __call__(self, f):
        score = super(ConsistentFragmentScorer, self).__call__(f)

        # line = f.text[f.startchar:f.endchar]
        if f.startchar:
            score += 1 / f.startchar
        return score