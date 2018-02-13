from flask import Flask
from flask_assets import Bundle, Environment
from webassets.filter.slimit import Slimit
from webassets.filter.cssutils import CSSUtils

app = Flask(__name__)
bundles = {
    'main_js': Bundle(
        'main.js',
        output='gen/main.js',
        filters=Slimit(mangle=True),
    ),
    'main_css': Bundle(
        'main.css',
        output='gen/main.css',
        filters=CSSUtils(),
    ),
}

env = Environment(app)
env.register(bundles)
