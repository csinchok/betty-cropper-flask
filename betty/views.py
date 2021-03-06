import os
import random
import copy
import shutil

from betty import app
from betty.database import db_session
from betty.models import Image as ImageObj
from betty.models import Ratio
from betty.crossdomain import crossdomain

from flask import abort, make_response, redirect, jsonify, request, current_app
from werkzeug import secure_filename
from wand.image import Image
from wand.color import Color
from wand.drawing import Drawing

BACKGROUND_COLORS = (
    "rgb(153,153,51)",
    "rgb(102,153,51)",
    "rgb(51,153,51)",
    "rgb(153,51,51)",
    "rgb(194,133,71)",
    "rgb(51,153,102)",
    "rgb(153,51,102)",
    "rgb(71,133,194)",
    "rgb(51,153,153)",
    "rgb(153,51,153)",
)

@app.errorhandler(404)
def page_not_found(e):
    return 'Not found', 404, {"Cache-Control": "max-age=60"}

@app.route('/<path:id>/<string:ratio_slug>/<int:width>.<string:extension>', methods=['GET'])
def crop(id, ratio_slug, width, extension):
    try:
        ratio = Ratio(ratio_slug)
    except ValueError:
        abort(404)

    if extension not in ('jpg', 'png'):
        abort(404)

    if width > 2000:
        abort(500)

    if len(id) > 4 and id == id.replace("/", ""):
        image_id = id.replace("/", "")
        id_string = ""
        for index,char in enumerate(image_id):
            if index % 4 == 0:
                id_string += "/"
            id_string += char
        # TODO: make this dynamic
        return redirect("/avclub%s/%s/%s.%s" % (id_string, ratio_slug, width, extension))

    try:
        image_id = int(id.replace("/", ""))
    except ValueError:
        abort(404)

    image = ImageObj.query.get(image_id)
    if image is None:
        if current_app.config['BETTY'].get('PLACEHOLDER', False):
            return placeholder(ratio, width, extension)
        else:
            abort(404)

    try:
        source_file = open(image.src_path(), 'r')
    except IOError:
        if current_app.config['BETTY'].get('PLACEHOLDER', False):
            return placeholder(ratio, width, extension)
        else:
            abort(404)

    with Image(file=source_file, format=extension) as img:
        if ratio_slug == 'original':
            ratio.width = img.size[0]
            ratio.height = img.size[1]

        selection = image.get_selection(ratio)
        try:
            img.crop(selection['x0'], selection['y0'], selection['x1'], selection['y1'])
        except ValueError:
            # Uhoh, looks like we have bad height and width data. Let's reload that and try again.
            image.width = img.size[0]
            image.height = img.size[1]
            db_session.add(image)
            db_session.commit()

            selection = image.get_selection(ratio)
            img.crop(selection['x0'], selection['y0'], selection['x1'], selection['y1'])


        img.transform(resize='%dx' % width)

        if extension == 'jpg':
            img.format = 'jpeg'
            img.compression_quality = 80
        if extension == 'png':
            img.format = 'png'

        img_blob = img.make_blob()

        ratio_dir = os.path.join(image.path(), ratio.string)
        try:
            os.makedirs(ratio_dir)
        except OSError as e:
            if e.errno != 17:
                abort(500)

        with open(os.path.join(ratio_dir, "%d.%s" % (width, extension)), 'w+') as out:
            out.write(img_blob)

        resp = make_response(img_blob, 200)
        if extension == 'jpg':
           resp.headers["Content-Type"] = "image/jpeg"
        if extension == 'png':
            resp.headers["Content-Type"] = "image/png"
        return resp
    abort(500)


def placeholder(ratio, width, extension):
    height = (width * ratio.height / float(ratio.width))
    with Drawing() as draw:
        draw.font = app.config['BETTY']['PLACEHOLDER_FONT']
        draw.font_size = 52
        draw.gravity = "center"
        draw.fill_color = Color("white")
        with Color(random.choice(BACKGROUND_COLORS)) as bg:
            with Image(width=width, height=int(height), background=bg) as img:
                draw.text(0, 0, ratio.string)
                draw(img)

                if extension == 'jpg':
                    img.format = 'jpeg'
                if extension == 'png':
                    img.format = 'png'

                img_blob = img.make_blob()

                resp = make_response(img_blob, 200)
                resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                resp.headers["Pragma"] = "no-cache"
                resp.headers["Expires"] = "0"
                if extension == 'jpg':
                   resp.headers["Content-Type"] = "image/jpeg"
                if extension == 'png':
                    resp.headers["Content-Type"] = "image/png"
                return resp

@app.route('/api/new', methods=['POST', 'OPTIONS'])
@crossdomain(origin='*')
def new():
    if not app.config['DEBUG'] and request.headers.get('X-Betty-Api-Key') != app.config['BETTY']['API_KEY']:
        response = jsonify({'message': 'Not authorized'})
        response.status_code = 403
        return response

    if 'image' not in request.files:
        abort(400)
    
    image_file = request.files['image']
    with Image(file=image_file) as img:
        width = img.size[0]
        height = img.size[1]

        filename = secure_filename(image_file.filename)
        
        image = ImageObj(name=filename, width=width, height=height, selections={})
        db_session.add(image)
        db_session.commit()

        os.makedirs(image.path())
        img.save(filename=os.path.join(image.path(), filename))
        os.symlink(filename, image.src_path())

    return jsonify(image.to_dict())

@app.route('/api/<int:id>/<string:ratio>', methods=['POST', 'OPTIONS'])
@crossdomain(origin='*')
def update_selection(id, ratio):
    # TODO: move this to a decorator or similar
    if not app.config['DEBUG'] and request.headers.get('X-Betty-Api-Key') != app.config['BETTY']['API_KEY']:
        response = jsonify({'message': 'Not authorized'})
        response.status_code = 403
        return response

    image = ImageObj.query.get(id)
    if image is None:
        response = jsonify({'message': 'No such image!', 'error': True})
        response.status_code = 404
        return response

    if request.json:
        try:
            selection = {
                'x0': int(request.json['x0']),
                'y0': int(request.json['y0']),
                'x1': int(request.json['x1']),
                'y1': int(request.json['y1']),
            }
        except (KeyError, ValueError):
            response = jsonify({'message': 'Bad selection', 'error': True})
            response.status_code = 400
            return response
    else:
        response = jsonify({'message': 'No selection', 'error': True})
        response.status_code = 400
        return response

    selections = copy.copy(image.selections)
    if selections is None:
        selections = {}

    if ratio not in current_app.config['BETTY']['RATIOS']:
        response = jsonify({'message': 'No such ratio', 'error': True})
        response.status_code = 400
        return response

    selections[ratio] = selection
    image.selections = selections
    db_session.add(image)
    db_session.commit()

    ratio_path = os.path.join(image.path(), ratio)
    if os.path.exists(ratio_path):
        crops = os.listdir(ratio_path)
        # TODO: flush cache on crops
        shutil.rmtree(ratio_path)

    return jsonify({'message': 'OK', 'error': False})
    
@app.route('/api/search', methods=['GET', 'OPTIONS'])
@crossdomain(origin='*')
def search():
    if not app.config['DEBUG'] and request.headers.get('X-Betty-Api-Key') != app.config['BETTY']['API_KEY']:
        response = jsonify({'message': 'Not authorized'})
        response.status_code = 403
        return response

    query = request.args.get('q')

    if query:
        q = db_session.query(ImageObj).filter(ImageObj.name.ilike('%' + query + '%')).order_by('-id').limit(25)
    else:
        q = db_session.query(ImageObj).order_by('-id').limit(25)
    results = []

    for instance in q:
        results.append(instance.to_dict())
    return jsonify({'results': results})

@app.route('/api/<int:id>', methods=['GET', 'OPTIONS', 'PATCH'])
@crossdomain(origin='*')
def image_detail(id):
    if not app.config['DEBUG'] and request.headers.get('X-Betty-Api-Key') != app.config['BETTY']['API_KEY']:
        response = jsonify({'message': 'Not authorized'})
        response.status_code = 403
        return response

    image = ImageObj.query.get(id)
    if image is None:
        abort(404)

    if request.method == 'PATCH':
        if request.json:
            if 'name' in request.json:
                image.name = request.json['name']
            if 'credit' in request.json:
                image.credit = request.json['credit']
            db_session.add(image)
            db_session.commit()
        else:
            response = jsonify({'message': 'No data', 'error': True})
            response.status_code = 400
            return response

    return jsonify(image.to_dict())
