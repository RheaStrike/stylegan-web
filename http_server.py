
import os
import sys
import io
import time
from dotenv import load_dotenv
import flask
import pickle
import PIL.Image
import base64
import numpy as np
import tensorflow.compat.v1 as tf
tf.disable_v2_behavior()
from threading import Lock
import json
import re

import dnnlib.tflib
from training import misc
from projector import Projector
import latentCode



load_dotenv(dotenv_path = './.env.local')
load_dotenv()


g_Gs = None
g_Synthesis = None
g_Lpips = None
g_Projector = None
g_Session = None
g_LoadingMutex = Lock()

def get_files_with_ext(path, extensions):
    files = os.listdir(path)
    files = [os.path.join(path,f) for f in files]
    files = [f for f in files if os.path.isfile(f)]
    files = [f for f in files if f.endswith(tuple(extensions))]
    return files

def get_latent_vector_files():
    files = get_files_with_ext('latent_directions', '.npy')
    # latent_vectors = [np.load(f) for f in files]
    return files


def load_latent_direction(modelName):
	files = get_files_with_ext('latent_directions', '.npy')
	latent_vectors = [np.load(f) for f in files]
	print("latent_vectors:",latent_vectors)
	print("TODO - add logic here :",modelName)



def loadGs():
	with g_LoadingMutex:
		global g_Gs, g_Synthesis
		if g_Gs:
			return g_Gs, g_Synthesis

		global model_name

		model_path = os.environ.get('MODEL_PATH_%s' % model_name)

		if model_path is None:
			print('invalid model name:', model_path)
			return

		global g_Session
		if g_Session is None:
			print('Initializing dnnlib...')
			dnnlib.tflib.init_tf()
			g_Session = tf.get_default_session()

		print('Loading model %s ...' % model_name)

		with open(model_path, 'rb') as f:
			with g_Session.as_default():
				Gi, Di, Gs = pickle.load(f)
				g_Gs = Gs

				## burn one
				#images = Gs.run(np.zeros((1, Gs.input_shape[1])), None, truncation_psi = 0.7, output_transform = dict(func = dnnlib.tflib.convert_images_to_uint8, nchw_to_nhwc = True))
				#print('images:', g_Session, images)

				#print('Gs.components.synthesis.input_shape:', Gs.components.synthesis.input_shape)
				global g_dLatentsIn
				g_dLatentsIn = tf.placeholder(tf.float32, [Gs.components.synthesis.input_shape[1] * Gs.input_shape[1]])
				dlatents_expr = tf.reshape(g_dLatentsIn, [1, Gs.components.synthesis.input_shape[1], Gs.input_shape[1]])
				g_Synthesis = Gs.components.synthesis.get_output_for(dlatents_expr, randomize_noise = False)

	return g_Gs, g_Synthesis


def loadLpips():
	with g_LoadingMutex:
		global g_Lpips
		if g_Lpips:
			return g_Lpips

		model_path = os.environ.get('MODEL_PATH_LPIPS')

		if model_path is None:
			print('invalid model name:', model_path)
			return

		global g_Session
		if g_Session is None:
			print('Initializing dnnlib...')
			dnnlib.tflib.init_tf()
			g_Session = tf.get_default_session()

		print('Loading model lpips ...')

		with open(model_path, 'rb') as f:
			with g_Session.as_default():
				lpips = pickle.load(f)
				g_Lpips = lpips

	return g_Lpips


def loadProjector():
	global g_Projector
	if g_Projector:
		return g_Projector

	gs, _ = loadGs()
	lpips = loadLpips()

	g_Projector = Projector()
	g_Projector.regularize_noise_weight = float(os.environ.get('REGULARIZE_NOISE_WEIGHT', 1e5))
	g_Projector.initial_noise_factor = float(os.environ.get('INITIAL_NOISE_FACTOR', 0.05))
	g_Projector.uniform_latents = int(os.environ.get('UNIFORM_LATENTS', 0)) > 0
	g_Projector.euclidean_dist_weight = float(os.environ.get('EUCLIDEAN_DIST_WEIGHT', 1))
	g_Projector.regularize_magnitude_weight = float(os.environ.get('REGULARIZE_MAGNITUDE_WEIGHT', 0))
	g_Projector.set_network(gs, lpips)

	return g_Projector


app = flask.Flask(__name__, static_url_path = '', static_folder = './static')


DIST_DIR = './dist'


@app.route('/bundles/<path:filename>')
def bundle(filename):
	if re.match(r'.*\.bundle\.js$', filename):
		return flask.send_from_directory(DIST_DIR, filename)

	flask.abort(404, 'Invalid request path.')


pageRouters = {
	'/':				'index.html',
	'/projector/':		'projector.html',
	'/merger/':			'merger.html',
	'/mapping-viewer/':	'mappingViewer.html',
}
for path in pageRouters:
	def getHandler(filename):
		return lambda: flask.send_from_directory(DIST_DIR, filename)

	app.route(path, endpoint = 'handler' + path)(getHandler(pageRouters[path]))

# /change?direction=smile
@app.route('/change', methods=['GET'])
def directions():
	directionsStr = flask.request.args.get('direction')
	print("directionsStr:",directionsStr)
	load_latent_direction(directionsStr)
	return {"status":directionsStr}


@app.route('/spec', methods=['GET'])
def spec():
	global model_name
	model, _ = loadGs()
	latent_directions = get_latent_vector_files()
	print("latent_directions:",latent_directions)
	return dict(
		model = model_name,
		latents_dimensions = model.input_shape[1],
		image_shape = model.output_shape,
		synthesis_input_shape = model.components.synthesis.input_shape,
		latent_directions = latent_directions)


@app.route('/map-z-w', methods=['GET'])
def mapZtoW():
	zStr = flask.request.args.get('z')
	psi = flask.request.args.get('psi')
	psi = psi and float(psi)

	gs, _ = loadGs()
	latent_len = gs.input_shape[1]

	z = latentCode.decodeFloat32(zStr, latent_len).reshape([1, latent_len])
	w = gs.components.mapping.run(z, None)[:, :1, :].reshape([latent_len])

	if psi is not None:
		proj = loadProjector()
		avg = proj._dlatent_avg.reshape([latent_len])

		w = avg + psi * (w - avg)

	return latentCode.encodeFloat32(w)


@app.route('/generate', methods=['GET'])
def generate():
	latentsStr = flask.request.args.get('latents')
	latentsStrX = flask.request.args.get('xlatents')
	psi = float(flask.request.args.get('psi', 0.5))
	#use_noise = bool(flask.request.args.get('use_noise', True))
	randomize_noise = int(flask.request.args.get('randomize_noise', 0))
	fromW = int(flask.request.args.get('fromW', 0))

	global g_Session
	global g_dLatentsIn
	#print('g_Session.1:', g_Session)

	gs, synthesis = loadGs()

	latent_len = gs.input_shape[1]
	if latentsStrX:
		latents = latentCode.decodeFixed16(latentsStrX, g_dLatentsIn.shape[0])
	else:
		latents = latentCode.decodeFloat32(latentsStr, latent_len)

	t0 = time.time()

	# Generate image.
	fmt = dict(func = dnnlib.tflib.convert_images_to_uint8, nchw_to_nhwc = True)
	with g_Session.as_default():
		if fromW != 0:
			#print('latentsStr:', latentsStr)
			#print('shapes:', g_dLatentsIn.shape, latents.shape)

			if latents.shape[0] < g_dLatentsIn.shape[0]:
				latents = np.tile(latents, g_dLatentsIn.shape[0] // latents.shape[0])
			images = dnnlib.tflib.run(synthesis, {g_dLatentsIn: latents})
			image = misc.convert_to_pil_image(misc.create_image_grid(images), drange = [-1,1])
		else:
			latents = latents.reshape([1, latent_len])
			images = gs.run(latents, None, truncation_psi = psi, randomize_noise = randomize_noise != 0, output_transform = fmt)
			image = PIL.Image.fromarray(images[0], 'RGB')

	print('generation cost:', time.time() - t0)

	# encode to PNG
	fp = io.BytesIO()
	image.save(fp, PIL.Image.registered_extensions()['.png'])

	return flask.Response(fp.getvalue(), mimetype = 'image/png')


#LPIPS_IMAGE_SHAPE = tuple(map(int, os.environ.get('LPIPS_IMAGE_SHAPE', '256,256').split(',')))


@app.route('/project', methods=['POST'])
def project():
	steps = int(flask.request.args.get('steps', 1000))
	yieldInterval = int(flask.request.args.get('yieldInterval', 10))
	#regularizeNoiseWeight = float(flask.request.args.get('regularizeNoiseWeight', 1e5))

	imageFile = flask.request.files.get('image')
	if not imageFile:
		flask.abort(400, 'image field is requested.')

	Gs, _ = loadGs()
	image = PIL.Image.open(imageFile.stream).resize((Gs.output_shape[2], Gs.output_shape[3]), PIL.Image.ANTIALIAS)

	image_array = np.array(image)[:, :, :3].swapaxes(0, 2).swapaxes(1, 2)
	image_array = misc.adjust_dynamic_range(image_array, [0, 255], [-1, 1])

	#print('shape:', image_array.shape)

	def gen():
		proj = loadProjector()
		#proj.regularize_noise_weight = regularizeNoiseWeight
		proj.start([image_array])
		for step in proj.runSteps(steps):
			print('\rProjecting: %d / %d' % (step, steps), end = '', flush = True)

			if step % yieldInterval == 0:
				dlatents = proj.get_dlatents()
				images = proj.get_images()
				pilImage = misc.convert_to_pil_image(misc.create_image_grid(images), drange = [-1,1])

				fp = io.BytesIO()
				pilImage.save(fp, PIL.Image.registered_extensions()['.png'])

				imgUrl = 'data:image/png;base64,%s' % base64.b64encode(fp.getvalue()).decode('ascii')

				#latentsList = list(dlatents.reshape((-1, dlatents.shape[2])))
				#latentCodes = list(map(lambda latents: latentCode.encodeFloat32(latents).decode('ascii'), latentsList))
				latentCodes = latentCode.encodeFixed16(dlatents.flatten()).decode('ascii')

				yield json.dumps(dict(step = step, img = imgUrl, latentCodes = latentCodes)) + '\n\n'

		print('\rProjecting finished.%s' % (' ' * 8))

	return flask.Response(gen(), mimetype = 'text/plain')


def main(argv):
	global model_name
	model_name = argv[1] if len(argv) > 1 else os.environ.get('MODEL_NAME')
	
	crt_path = os.environ.get('SSL_CERT_PATH')
	key_path = os.environ.get('SSL_KEY_PATH')
	context = (crt_path, key_path) if crt_path and key_path else None

	try:
		app.run(port = int(os.getenv('HTTP_PORT')), host = os.getenv('HTTP_HOST'), threaded = False, ssl_context=context)
	except:
		print('server interrupted:', sys.exc_info())



if __name__ == "__main__":
	print("\n\n\n!!!!!!WARNING -FileNotFoundError?? you need to run generate_cert.sh to create ssl certs / stylegan-web.crt and update /etc/hosts - 127.0.0.1 example.com (no www) !!!!")
	main(sys.argv)
