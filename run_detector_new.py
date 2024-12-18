########
#
# run_detector.py
#
# Module to run an animal detection model on images.
# 
# The main function in this script also renders the predicted
# bounding boxes on images and saves the resulting images (with bounding boxes).
# 
# This script is not a good way to process lots of images (tens of thousands,
# say). It does not facilitate checkpointing the results so if it crashes you
# would have to start from scratch. If you want to run a detector (e.g., ours)
# on lots of images, you should check out run_detector_batch.py.
# 
# To run this script, we recommend you set up a conda virtual environment
# following instructions in the Installation section on the main README, using
# `environment-detector.yml` as the environment file where asked.
# 
# This is a good way to test our detector on a handful of images and get
# super-satisfying, graphical results.  It's also a good way to see how fast a
# detector model will run on a particular machine.
# 
# If you would like to *not* use the GPU on the machine, set the environment
# variable CUDA_VISIBLE_DEVICES to "-1".
# 
# If no output directory is specified, writes detections for c:\foo\bar.jpg to
# c:\foo\bar_detections.jpg.
# 
# This script will only consider detections with > 0.005 confidence at all times.
# The `threshold` you provide is only for rendering the results. If you need to
# see lower-confidence detections, you can change
# DEFAULT_OUTPUT_CONFIDENCE_THRESHOLD.
# 
# Reference:
# https://github.com/tensorflow/models/blob/master/research/object_detection/inference/detection_inference.py
# Edited By Brendan Karadenes
########

#%% Constants, imports, environment

import argparse
import os
import statistics
import sys
import time
import warnings

import humanfriendly
from tqdm import tqdm

import md_utils.path_utils as path_utils
import md_visualization.visualization_utils as vis_utils

# ignoring all "PIL cannot read EXIF metainfo for the images" warnings
warnings.filterwarnings('ignore', '(Possibly )?corrupt EXIF data', UserWarning)

# Metadata Warning, tag 256 had too many entries: 42, expected 1
warnings.filterwarnings('ignore', 'Metadata warning', UserWarning)

# Numpy FutureWarnings from tensorflow import
warnings.filterwarnings('ignore', category=FutureWarning)

# Useful hack to force CPU inference
# os.environ['CUDA_VISIBLE_DEVICES'] = '-1'


# An enumeration of failure reasons
FAILURE_INFER = 'Failure inference'
FAILURE_IMAGE_OPEN = 'Failure image access'

# Number of decimal places to round to for confidence and bbox coordinates
CONF_DIGITS = 3
COORD_DIGITS = 4

# Label mapping for MegaDetector
DEFAULT_DETECTOR_LABEL_MAP = {
    '1': 'animal',
    '2': 'person',
    '3': 'vehicle'  # available in megadetector v4+
}

# Should we allow classes that don't look anything like the MegaDetector classes?
#
# By default, we error if we see unfamiliar classes.
#
# TODO: the use of a global variable to manage this was fine when this was really
# experimental, but this is really sloppy now that we actually use this code for
# models other than MegaDetector.
USE_MODEL_NATIVE_CLASSES = False

# Each version of the detector is associated with some "typical" values
# that are included in output files, so that downstream applications can 
# use them as defaults.
DETECTOR_METADATA = {
    'v2.0.0':
        {'megadetector_version':'v2.0.0',
         'typical_detection_threshold':0.8,
         'conservative_detection_threshold':0.3},
    'v3.0.0':
        {'megadetector_version':'v3.0.0',
         'typical_detection_threshold':0.8,
         'conservative_detection_threshold':0.3},
    'v4.1.0':
        {'megadetector_version':'v4.1.0',
         'typical_detection_threshold':0.8,
         'conservative_detection_threshold':0.3},
    'v5a.0.0':
        {'megadetector_version':'v5a.0.0',
         'typical_detection_threshold':0.2,
         'conservative_detection_threshold':0.05},
    'v5b.0.0':
        {'megadetector_version':'v5b.0.0',
         'typical_detection_threshold':0.2,
         'conservative_detection_threshold':0.05}
}

DEFAULT_RENDERING_CONFIDENCE_THRESHOLD = DETECTOR_METADATA['v5b.0.0']['typical_detection_threshold']
DEFAULT_OUTPUT_CONFIDENCE_THRESHOLD = 0.005

DEFAULT_BOX_THICKNESS = 4
DEFAULT_BOX_EXPANSION = 0
DEFAULT_LABEL_FONT_SIZE = 16
DETECTION_FILENAME_INSERT = '_detections'

# The model filenames "MDV5A", "MDV5B", and "MDV4" are special; they will trigger an 
# automatic model download to the system temp folder, or they will use the paths specified in the 
# $MDV4, $MDV5A, or $MDV5B environment variables if they exist.
downloadable_models = {
    'MDV4':'https://github.com/agentmorris/MegaDetector/releases/download/v4.1/md_v4.1.0.pb',
    'MDV5A':'https://github.com/agentmorris/MegaDetector/releases/download/v5.0/md_v5a.0.0.pt',
    'MDV5B':'https://github.com/agentmorris/MegaDetector/releases/download/v5.0/md_v5b.0.0.pt'
}

model_string_to_model_version = {
    'v2':'v2.0.0',
    'v3':'v3.0.0',
    'v4.1':'v4.1.0',
    'v5a.0.0':'v5a.0.0',
    'v5b.0.0':'v5b.0.0',
    'mdv5a':'v5a.0.0',
    'mdv5b':'v5b.0.0',
    'mdv4':'v4.1.0',
    'mdv3':'v3.0.0'
}

# Approximate inference speeds (in images per second) for MDv5 based on 
# benchmarks, only used for reporting very coarse expectations about inference time.
device_token_to_mdv5_inference_speed = {
    '4090':17.6,
    '3090':11.4,
    '3080':9.5,
    '3050':4.2,
    'P2000':2.1,
    # These are written this way because they're MDv4 benchmarks, and MDv5
    # is around 3.5x faster than MDv4.
    'V100':2.79*3.5,
    '2080':2.3*3.5,
    '2060':1.6*3.5
}


#%% Utility functions

def convert_to_tf_coords(array):
    """
    From [x1, y1, width, height] to [y1, x1, y2, x2], where x1 is x_min, x2 is x_max

    This is only used to keep the interface of the synchronous API.
    """

    x1 = array[0]
    y1 = array[1]
    width = array[2]
    height = array[3]
    x2 = x1 + width
    y2 = y1 + height
    return [y1, x1, y2, x2]


def get_detector_metadata_from_version_string(detector_version):
    """
    Given a MegaDetector version string (e.g. "v4.1.0"), return the metadata for
    the model.  Used for writing standard defaults to batch output files.
    """

    if detector_version not in DETECTOR_METADATA:
        print('Warning: no metadata for unknown detector version {}'.format(detector_version))
        default_detector_metadata = {
            'megadetector_version':'unknown',
            'typical_detection_threshold':0.5,
            'conservative_detection_threshold':0.25
        }
        return default_detector_metadata
    else:
        return DETECTOR_METADATA[detector_version]


def get_detector_version_from_filename(detector_filename):
    """
    Get the version number component of the detector from the model filename.  
    
    *detector_filename* will almost always end with one of the following:
        
    megadetector_v2.pb
    megadetector_v3.pb
    megadetector_v4.1 (not produed by run_detector_batch.py, only found in Azure Batch API output files)
    md_v4.1.0.pb
    md_v5a.0.0.pt
    md_v5b.0.0.pt
    
    ...for which we identify the version number as "v2.0.0", "v3.0.0", "v4.1.0", 
    "v4.1.0", "v5a.0.0", and "v5b.0.0", respectively.
    """

    fn = os.path.basename(detector_filename).lower()
    matches = []
    for s in model_string_to_model_version.keys():
        if s in fn:
            matches.append(s)
    if len(matches) == 0:
        print('Warning: could not determine MegaDetector version for model file {}'.format(detector_filename))
        return 'unknown'
    elif len(matches) > 1:
        print('Warning: multiple MegaDetector versions for model file {}'.format(detector_filename))
        return 'multiple'
    else:
        return model_string_to_model_version[matches[0]]


def estimate_md_images_per_second(model_file, device_name=None):
    """
    Estimate how fast MegaDetector will run based on benchmarks.  Defaults to querying
    the current device.  Returns None if no data is available for the current card/model.
    Estimates only available for a small handful of GPUs.
    """

    if device_name is None:
        try:
            import torch
            device_name = torch.cuda.get_device_name()
        except Exception as e:
            print('Error querying device name: {}'.format(e))
            return None

    model_file = model_file.lower().strip()
    if model_file in model_string_to_model_version.values():
        model_version = model_file
    else:
        model_version = get_detector_version_from_filename(model_file)
        if model_version not in model_string_to_model_version.values():
            print('Error determining model version for model file {}'.format(model_file))
            return None

    mdv5_inference_speed = None
    for device_token in device_token_to_mdv5_inference_speed.keys():
        if device_token in device_name:
            mdv5_inference_speed = device_token_to_mdv5_inference_speed[device_token]
            break

    if mdv5_inference_speed is None:
        print('No speed estimate available for {}'.format(device_name))

    if 'v5' in model_version:
        return mdv5_inference_speed
    elif 'v2' in model_version or 'v3' in model_version or 'v4' in model_version:
        return mdv5_inference_speed / 3.5
    else:
        print('Could not estimate inference speed for model file {}'.format(model_file))
        return None


def get_typical_confidence_threshold_from_results(results):
    """
    Given the .json data loaded from a MD results file, determine a typical confidence
    threshold based on the detector version.
    """

    if 'detector_metadata' in results['info'] and \
        'typical_detection_threshold' in results['info']['detector_metadata']:
        default_threshold = results['info']['detector_metadata']['typical_detection_threshold']
    elif ('detector' not in results['info']) or (results['info']['detector'] is None):
        print('Warning: detector version not available in results file, using MDv5 defaults')
        detector_metadata = get_detector_metadata_from_version_string('v5a.0.0')
        default_threshold = detector_metadata['typical_detection_threshold']
    else:
        print('Warning: detector metadata not available in results file, inferring from MD version')
        detector_filename = results['info']['detector']
        detector_version = get_detector_version_from_filename(detector_filename)
        detector_metadata = get_detector_metadata_from_version_string(detector_version)
        default_threshold = detector_metadata['typical_detection_threshold']

    return default_threshold


def is_gpu_available(model_file):
    """
    Decide whether a GPU is available, importing PyTorch or TF depending on the extension
    of model_file.  Does not actually load model_file, just uses that to determine how to check 
    for GPU availability.
    """

    if model_file.endswith('.pb'):
        import tensorflow.compat.v1 as tf
        gpu_available = tf.test.is_gpu_available()
        print('TensorFlow version:', tf.__version__)
        print('tf.test.is_gpu_available:', gpu_available)
        return gpu_available
    elif model_file.endswith('.pt'):
        import torch
        gpu_available = torch.cuda.is_available()
        print('PyTorch reports {} available CUDA devices'.format(torch.cuda.device_count()))
        if not gpu_available:
            try:
                # mps backend only available in torch >= 1.12.0
                if torch.backends.mps.is_built and torch.backends.mps.is_available():
                    gpu_available = True
                    print('PyTorch reports Metal Performance Shaders are available')
            except AttributeError:
                pass
        return gpu_available
    else:
        raise ValueError('Unrecognized model file extension for model {}'.format(model_file))


def load_detector(model_file, force_cpu=False):
    """
    Load a TF or PT detector, depending on the extension of model_file.
    """

    # Possibly automatically download the model
    model_file = try_download_known_detector(model_file)

    start_time = time.time()
    if model_file.endswith('.pb'):
        from detection.tf_detector import TFDetector
        if force_cpu:
            raise ValueError('force_cpu is not currently supported for TF detectors, ' + \
                             'use CUDA_VISIBLE_DEVICES=-1 instead')
        detector = TFDetector(model_file)
    elif model_file.endswith('.pt'):
        from detection.pytorch_detector import PTDetector
        detector = PTDetector(model_file, force_cpu, USE_MODEL_NATIVE_CLASSES)
    else:
        raise ValueError('Unrecognized model format: {}'.format(model_file))
    elapsed = time.time() - start_time
    print('Loaded model in {}'.format(humanfriendly.format_timespan(elapsed)))
    return detector


#%% Main function

# adding input directory into the load and run detector function

def load_and_run_detector(model_file, input_dir, image_file_names, output_dir,
                          render_confidence_threshold=DEFAULT_RENDERING_CONFIDENCE_THRESHOLD,
                          crop_images=False, box_thickness=DEFAULT_BOX_THICKNESS,
                          box_expansion=DEFAULT_BOX_EXPANSION, image_size=None,
                          label_font_size=DEFAULT_LABEL_FONT_SIZE, animal_threshold = 0.8,
                          person_threshold = 0.8, vehicle_threshold = 0.8
                          ):
    # Create output directories that match structure of input directories
    input_subdirs = [d for d in os.listdir(input_dir) if os.path.isdir(os.path.join(input_dir, d))]
    output_subdirs = {}
    for subdir in input_subdirs:
        input_path = os.path.join(input_dir, subdir)
        output_path = os.path.join(output_dir, subdir)
        os.makedirs(output_path, exist_ok=True)
        output_subdirs[subdir] = output_path

# old code to put each category into different categories

 #   animal_dir = os.path.join(output_dir, 'animals')
  #  person_dir = os.path.join(output_dir, 'people')
  #  vehicle_dir = os.path.join(output_dir, 'vehicles')
  #  os.makedirs(animal_dir, exist_ok=True)
  #  os.makedirs(person_dir, exist_ok=True)
  #  os.makedirs(vehicle_dir, exist_ok=True)
    """
    Load and run detector on target images, and visualize the results.
    """

    if len(image_file_names) == 0:
        print('Warning: no files available')
        return

    # Possibly automatically download the model
    model_file = try_download_known_detector(model_file)

    print('GPU available: {}'.format(is_gpu_available(model_file)))

    detector = load_detector(model_file)

    detection_results = []
    # placing outputs into subdirectories
    for subdir, output_path in output_subdirs.items():
        input_path = os.path.join(input_dir, subdir)
        image_files = [os.path.join(input_path, f) for f in os.listdir(input_path) if
                       os.path.isfile(os.path.join(input_path, f))]
        for im_file in tqdm(image_files, desc=f'Processing {subdir}'):

            try:
                # Load image
                image = vis_utils.load_image(im_file)

                # Generate detections
                result = detector.generate_detections_one_image(
                    image, im_file, detection_threshold=DEFAULT_OUTPUT_CONFIDENCE_THRESHOLD, image_size=image_size)
                detection_results.append(result)

                # Process detections
                save_image = False
                for detection in result['detections']:
                    category = detection['category']
                    confidence = float(detection['conf'])
                    if (
                            (category == '1' and confidence >= animal_threshold) or
                            (category == '2' and confidence >= person_threshold) or
                            (category == '3' and confidence >= vehicle_threshold)
                    ):
                        save_image = True
                        break  # Save image if any detection passes the threshold

                # Save if any detection passes the threshold
                if save_image:
                    output_filename = os.path.join(output_path, os.path.basename(im_file))
                    if crop_images:
                        cropped_images = vis_utils.crop_image(result['detections'], image,
                                                              confidence_threshold=render_confidence_threshold,
                                                              expansion=box_expansion)
                        for i, cropped_image in enumerate(cropped_images):
                            cropped_filename = output_filename.replace('.jpg', f'_crop{i}.jpg')
                            cropped_image.save(cropped_filename)
                    else:
                        vis_utils.render_detection_bounding_boxes(result['detections'], image,
                                                                  label_map=DEFAULT_DETECTOR_LABEL_MAP,
                                                                  confidence_threshold=render_confidence_threshold,
                                                                  thickness=box_thickness, expansion=box_expansion,
                                                                  label_font_size=label_font_size)
                        image.save(output_filename)
            except Exception as e:
                print(f'Error processing image {im_file}: {e}')
    time_load = []
    time_infer = []

    # Dictionary mapping output file names to a collision-avoidance count.
    #
    # Since we'll be writing a bunch of files to the same folder, we rename
    # as necessary to avoid collisions.
    output_filename_collision_counts = {}
    # moved output_dir to first parameter

    def input_file_to_detection_file(output_dir, fn, crop_index=-1):
        """
        Creates unique file names for output files.

        This function does 3 things:
        1) If the --crop flag is used, then each input image may produce several output
            crops. For example, if foo.jpg has 3 detections, then this function should
            get called 3 times, with crop_index taking on 0, 1, then 2. Each time, this
            function appends crop_index to the filename, resulting in
                foo_crop00_detections.jpg
                foo_crop01_detections.jpg
                foo_crop02_detections.jpg

        2) If the --recursive flag is used, then the same file (base)name may appear
            multiple times. However, we output into a single flat folder. To avoid
            filename collisions, we prepend an integer prefix to duplicate filenames:
                foo_crop00_detections.jpg
                0000_foo_crop00_detections.jpg
                0001_foo_crop00_detections.jpg

        3) Prepends the output directory:
                out_dir/foo_crop00_detections.jpg

        Args:
            fn: str, filename
            crop_index: int, crop number

        Returns: output file path
        """
        # getting error because filenames were not unique
        # Visualizing results on the image C:\Users\bkara\Downloads\temp\fox.jpg failed.
        fn = os.path.basename(fn).lower()
        name, ext = os.path.splitext(fn)
        if crop_index >= 0:
            name += '_crop{:0>2d}'.format(crop_index)
        fn = '{}{}{}'.format(name, DETECTION_FILENAME_INSERT, '.jpg')
        if fn in output_filename_collision_counts:
            n_collisions = output_filename_collision_counts.get(fn, 0)  # get number of collisions
            # integer prefix to handle collisions
            fn = '{:0>4d}_{}'.format(n_collisions, fn)
            # update collision counts
            output_filename_collision_counts[fn] = n_collisions + 1
            fn = os.path.join(output_dir, fn)
            return fn
        else:
            output_filename_collision_counts[fn] = 0
        fn = os.path.join(output_dir, fn)
        return fn

    # ...def input_file_to_detection_file()

    for im_file in tqdm(image_file_names):

        try:
            start_time = time.time()

            image = vis_utils.load_image(im_file)

            elapsed = time.time() - start_time
            time_load.append(elapsed)

        except Exception as e:
            print('Image {} cannot be loaded. Exception: {}'.format(im_file, e))
            result = {
                'file': im_file,
                'failure': FAILURE_IMAGE_OPEN
            }
            detection_results.append(result)
            continue

        try:
            start_time = time.time()

            result = detector.generate_detections_one_image(image, im_file,
                       detection_threshold=DEFAULT_OUTPUT_CONFIDENCE_THRESHOLD,
                       image_size=image_size)
            detection_results.append(result)

            elapsed = time.time() - start_time
            time_infer.append(elapsed)
            # New code: print out the confidence interval
            for detection in result['detections']:
                if detection['category'] == '1':
                    confidence = detection['conf']
                    print(f"Detection confidence: {confidence:.3f}")

        except Exception as e:
            print('An error occurred while running the detector on image {}. Exception: {}'.format(im_file, e))
            continue

        try:


            if crop_images:

                images_cropped = vis_utils.crop_image(result['detections'], image,
                                   confidence_threshold=render_confidence_threshold,
                                   expansion=box_expansion)

                for i_crop, cropped_image in enumerate(images_cropped):
                    output_full_path = input_file_to_detection_file(im_file, i_crop)
                    cropped_image.save(output_full_path)

            else:

                # Image is modified in place
                vis_utils.render_detection_bounding_boxes(result['detections'], image,
                            label_map=DEFAULT_DETECTOR_LABEL_MAP,
                            confidence_threshold=render_confidence_threshold,
                            thickness=box_thickness, expansion=box_expansion,
                            label_font_size=label_font_size)
                output_full_path = input_file_to_detection_file(im_file)
                image.save(output_full_path)

        except Exception as e:
            print('Visualizing results on the image {} failed. Exception: {}'.format(im_file, e))
            continue

    # ...for each image

    ave_time_load = statistics.mean(time_load)
    ave_time_infer = statistics.mean(time_infer)
    if len(time_load) > 1 and len(time_infer) > 1:
        std_dev_time_load = humanfriendly.format_timespan(statistics.stdev(time_load))
        std_dev_time_infer = humanfriendly.format_timespan(statistics.stdev(time_infer))
    else:
        std_dev_time_load = 'not available'
        std_dev_time_infer = 'not available'
    print('On average, for each image,')
    print('- loading took {}, std dev is {}'.format(humanfriendly.format_timespan(ave_time_load),
                                                    std_dev_time_load))
    print('- inference took {}, std dev is {}'.format(humanfriendly.format_timespan(ave_time_infer),
                                                      std_dev_time_infer))

# ...def load_and_run_detector()


def download_model(model_name,force_download=False):
    """
    Download one of the known models to local temp space if it hasn't already been downloaded
    """

    import tempfile
    from md_utils.url_utils import download_url
    model_tempdir = os.path.join(tempfile.gettempdir(), 'megadetector_models')
    os.makedirs(model_tempdir,exist_ok=True)

    # This is a lazy fix to an issue... if multiple users run this script, the
    # "megadetector_models" folder is owned by the first person who creates it, and others
    # can't write to it.  I could create uniquely-named folders, but I philosophically prefer
    # to put all the individual UUID-named folders within a larger folder, so as to be a 
    # good tempdir citizen.  So, the lazy fix is to make this world-writable.
    try:
        os.chmod(model_tempdir,0o777)
    except Exception:
        pass
    if model_name not in downloadable_models:
        print('Unrecognized downloadable model {}'.format(model_name))
        return None
    url = downloadable_models[model_name]
    destination_filename = os.path.join(model_tempdir,url.split('/')[-1])
    local_file = download_url(url, destination_filename=destination_filename, progress_updater=None,
                     force_download=force_download, verbose=True)
    return local_file


def try_download_known_detector(detector_file):
    """
    Check whether detector_file is really the name of a known model, in which case we will
    either read the actual filename from the corresponding environment variable or download
    (if necessary) to local temp space.  Otherwise just returns the input string.
    """

    if detector_file in downloadable_models:
        if detector_file in os.environ:
            fn = os.environ[detector_file]
            print('Reading MD location from environment variable {}: {}'.format(
                detector_file,fn))
            detector_file = fn
        else:
            print('Downloading model {}'.format(detector_file))
            detector_file = download_model(detector_file)
    return detector_file



#%% Command-line driver

def main():
    parser = argparse.ArgumentParser(
        description='Module to run metadetector on images')



    # Must specify either an image file or a directory
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--image_file',
        type=str,
        default=None,
        help='Single file to process, mutually exclusive with --image_dir')
    group.add_argument(
        '--image_dir',
        type=str,
        default=None,
        help='Directory to search for images, with optional recursion by adding --recursive')

    parser.add_argument(
        '--recursive',
        action='store_true',
        help='Recurse into directories, only meaningful if using --image_dir')

    parser.add_argument(
        '--output_dir',
        type=str,
        default=None,
        help='Directory for output images (defaults to same as input)')

    parser.add_argument(
        '--image_size',
        type=int,
        default=None,
        help=('Force image resizing to a (square) integer size (not recommended to change this)'))

    parser.add_argument(
        '--threshold',
        type=float,
        default=DEFAULT_RENDERING_CONFIDENCE_THRESHOLD,
        help=('Confidence threshold between 0 and 1.0; only render' +
              ' boxes above this confidence (defaults to {})'.format(
              DEFAULT_RENDERING_CONFIDENCE_THRESHOLD)))

    parser.add_argument(
        '--crop',
        default=False,
        action="store_true",
        help=('If set, produces separate output images for each crop, '
              'rather than adding bounding boxes to the original image'))

    parser.add_argument(
        '--box_thickness',
        type=int,
        default=DEFAULT_BOX_THICKNESS,
        help=('Line width (in pixels) for box rendering (defaults to {})'.format(
              DEFAULT_BOX_THICKNESS)))

    parser.add_argument(
        '--box_expansion',
        type=int,
        default=DEFAULT_BOX_EXPANSION,
        help=('Number of pixels to expand boxes by (defaults to {})'.format(
              DEFAULT_BOX_EXPANSION)))

    parser.add_argument(
        '--label_font_size',
        type=int,
        default=DEFAULT_LABEL_FONT_SIZE,
        help=('Label font size (defaults to {})'.format(
              DEFAULT_LABEL_FONT_SIZE)))

    parser.add_argument(
        '--animal_threshold', type=float, default=0.8, help="Confidence threshold for animals"
    )
    parser.add_argument(
        '--person_threshold', type=float, default=0.8, help="Confidence threshold for people"
    )
    parser.add_argument(
        '--vehicle_threshold', type=float, default=0.8, help="Confidence threshold for vehicles"
    )

    if len(sys.argv[1:]) == 0:
        parser.print_help()
        parser.exit()

    args = parser.parse_args()

    # If the specified detector file is really the name of a known model, find 
    # (and possibly download) that model
    args.detector_file = try_download_known_detector(args.detector_file)

    assert os.path.exists(args.detector_file), 'detector file {} does not exist'.format(
        args.detector_file)
    assert 0.0 < args.threshold <= 1.0, 'Confidence threshold needs to be between 0 and 1'

    if args.image_file:
        image_file_names = [args.image_file]
    else:
        image_file_names = path_utils.find_images(args.image_dir, args.recursive)

    print('Running detector on {} images...'.format(len(image_file_names)))

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
    else:
        if args.image_dir:
            args.output_dir = args.image_dir
        else:
            # but for a single image, args.image_dir is also None
            args.output_dir = os.path.dirname(args.image_file)

    load_and_run_detector(model_file=args.detector_file,
                          image_file_names=image_file_names,
                          output_dir=args.output_dir,
                          render_confidence_threshold=args.threshold,
                          box_thickness=args.box_thickness,
                          box_expansion=args.box_expansion,
                          crop_images=args.crop,
                          image_size=args.image_size,
                          label_font_size=args.label_font_size)


if __name__ == '__main__':
#    main()
    #%%
    model_file = "C:\\Users\\bkara\\Downloads\\md_v5a.0.0.pt"
    input_dir = r"C:\\Users\\bkara\\Downloads\\input"
    image_file_names = path_utils.find_images("C:\\Users\\bkara\\Downloads\\input")
    print(image_file_names)
   # image_file_names.append("C:\\Users\f\bkara\\Downloads\\temp\\Large.jpg")
    output_dir = "C:\\Users\\bkara\\Downloads\\output"
    render_confidence_threshold = 0.8
    crop_images = True
    image_file_names = []
    for subdir in os.listdir(input_dir):
        sub_path = os.path.join(input_dir, subdir)
        if os.path.isdir(sub_path):
            image_file_names.extend(path_utils.find_images(sub_path))

        # Ensure the output directory structure matches input structure
        os.makedirs(output_dir, exist_ok=True)


    load_and_run_detector(model_file=model_file,
                          input_dir = input_dir,
                          image_file_names=image_file_names,
                          output_dir=output_dir,
                          render_confidence_threshold=render_confidence_threshold,
                          crop_images=crop_images)



#%% Interactive driver

if False:

    #%%
    model_file = r'c:\temp\models\md_v4.1.0.pb'
    input_dir = r"C:\\Users\\bkara\\Downloads\\input"
    image_file_names = path_utils.find_images(r'c:\temp\demo_images\ssverymini')
    output_dir = r'c:\temp\demo_images\ssverymini'
    render_confidence_threshold = 0.8
    crop_images = True

    load_and_run_detector(model_file=model_file,
                          input_dir = input_dir,
                          image_file_names=image_file_names,
                          output_dir=output_dir,
                          render_confidence_threshold=render_confidence_threshold,
                          crop_images=crop_images)




