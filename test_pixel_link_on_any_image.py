#encoding = utf-8

import numpy as np
import math
import tensorflow as tf
from tensorflow.python.ops import control_flow_ops
from tensorflow.contrib.training.python.training import evaluation
from datasets import dataset_factory
from preprocessing import ssd_vgg_preprocessing
from tf_extended import metrics as tfe_metrics
import util
import cv2
import pixel_link
from nets import pixel_link_symbol


slim = tf.contrib.slim
import config
# =========================================================================== #
# Checkpoint and running Flags
# =========================================================================== #
tf.app.flags.DEFINE_string('checkpoint_path', None, 
   'the path of pretrained model to be used. If there are checkpoints\
    in train_dir, this config will be ignored.')

tf.app.flags.DEFINE_float('gpu_memory_fraction', -1, 
  'the gpu memory fraction to be used. If less than 0, allow_growth = True is used.')


# =========================================================================== #
# Dataset Flags.
# =========================================================================== #
tf.app.flags.DEFINE_string(
    'dataset_dir', 'None', 
    'The directory where the dataset files are stored.')

tf.app.flags.DEFINE_integer('eval_image_width', None, 'resized image width for inference')
tf.app.flags.DEFINE_integer('eval_image_height',  None, 'resized image height for inference')
tf.app.flags.DEFINE_float('pixel_conf_threshold',  None, 'threshold on the pixel confidence')
tf.app.flags.DEFINE_float('link_conf_threshold',  None, 'threshold on the link confidence')


tf.app.flags.DEFINE_bool('using_moving_average', True, 
                         'Whether to use ExponentionalMovingAverage')
tf.app.flags.DEFINE_float('moving_average_decay', 0.9999, 
                          'The decay rate of ExponentionalMovingAverage')


FLAGS = tf.app.flags.FLAGS

def config_initialization():
    # image shape and feature layers shape inference
    image_shape = (FLAGS.eval_image_height, FLAGS.eval_image_width)
    
    if not FLAGS.dataset_dir:
        raise ValueError('You must supply the dataset directory with --dataset_dir')
    
    tf.logging.set_verbosity(tf.logging.DEBUG)
    
    config.init_config(image_shape, 
                       batch_size = 1, 
                       pixel_conf_threshold = FLAGS.pixel_conf_threshold,
                       link_conf_threshold = FLAGS.link_conf_threshold,
                       num_gpus = 1, 
                   )


def test():
    checkpoint_dir = util.io.get_dir(FLAGS.checkpoint_path)
    
    global_step = slim.get_or_create_global_step()
    with tf.name_scope('evaluation_%dx%d'%(FLAGS.eval_image_height, FLAGS.eval_image_width)):
        with tf.variable_scope(tf.get_variable_scope(), reuse = False):
            image = tf.placeholder(dtype=tf.int32, shape = [None, None, 3])
            image_shape = tf.placeholder(dtype = tf.int32, shape = [3, ])
            processed_image, _, _, _, _ = ssd_vgg_preprocessing.preprocess_image(image, None, None, None, None, 
                                                       out_shape = config.image_shape,
                                                       data_format = config.data_format, 
                                                       is_training = False)
            b_image = tf.expand_dims(processed_image, axis = 0)

            # build model and loss
            net = pixel_link_symbol.PixelLinkNet(b_image, is_training = False)
            masks = pixel_link.tf_decode_score_map_to_mask_in_batch(
                net.pixel_pos_scores, net.link_pos_scores)
            
    sess_config = tf.ConfigProto(log_device_placement = False, allow_soft_placement = True)
    if FLAGS.gpu_memory_fraction < 0:
        sess_config.gpu_options.allow_growth = True
    elif FLAGS.gpu_memory_fraction > 0:
        sess_config.gpu_options.per_process_gpu_memory_fraction = FLAGS.gpu_memory_fraction;
    
    # Variables to restore: moving avg. or normal weights.
    if FLAGS.using_moving_average:
        variable_averages = tf.train.ExponentialMovingAverage(
                FLAGS.moving_average_decay)
        variables_to_restore = variable_averages.variables_to_restore(
                tf.trainable_variables())
        variables_to_restore[global_step.op.name] = global_step
    else:
        variables_to_restore = slim.get_variables_to_restore()
        
    
    saver = tf.train.Saver(var_list = variables_to_restore)
    with tf.Session() as sess:
        saver.restore(sess, util.tf.get_latest_ckpt(FLAGS.checkpoint_path))
        
        files = util.io.ls(FLAGS.dataset_dir)
        
        for image_name in files:
            file_path = util.io.join_path(FLAGS.dataset_dir, image_name)
            image_data = util.img.imread(file_path)
            link_scores, pixel_scores, mask_vals = sess.run(
                    [net.link_pos_scores, net.pixel_pos_scores, masks],
                    feed_dict = {image: image_data})
            h, w, _ =image_data.shape
            def resize(img):
                return util.img.resize(img, size = (w, h), 
                                       interpolation = cv2.INTER_NEAREST)
            
            def get_bboxes(mask):
                return pixel_link.mask_to_bboxes(mask, image_data.shape)
            
            def draw_bboxes(img, bboxes, color):
                for bbox in bboxes:
                    points = np.reshape(bbox, [4, 2])
                    cnts = util.img.points_to_contours(points)
                    util.img.draw_contours(img, contours = cnts, 
                           idx = -1, color = color, border_width = 1)
            image_idx = 0
            pixel_score = pixel_scores[image_idx, ...]
            mask = mask_vals[image_idx, ...]

            bboxes_det = get_bboxes(mask)
            
            mask = resize(mask)
            pixel_score = resize(pixel_score)

            
            import os
            ID = file_path.split('/')[-1].split('.')[0]
            '''
            txt_file = os.path.join('/data/VOC/train/tax_2/Txts3', '%s.txt' % ID)
            with open(txt_file, 'w') as f:
                count = 0
                for box in bboxes_det:
                    count = 1
                    x1, y1, x2, y2, x3, y3, x4, y4 = box
                    l_1 = int(math.sqrt((x1-x2)**2  (y1-y2)**2))
                    l_2 = int(math.sqrt((x2-x3)**2  (y2-y3)**2))

                    pts1 = np.float32([[box[0], box[1]], [box[2], box[3]], [box[6], box[7]], [box[4], box[5]]])

                    if l_1 < l_2:
                        width = l_2
                        height = l_1
                        pts2 = np.float32([[0, 0], [height, 0], [0, width], [height, width]])
                        M = cv2.getPerspectiveTransform(pts1, pts2)
                        ROI = cv2.warpPerspective(image_data, M, (height, width))
                        ROI = np.rot90(ROI)
                    else:
                        width = l_1
                        height = l_2
                        pts2 = np.float32([[0, 0], [width, 0], [0, height], [width, height]])
                        M = cv2.getPerspectiveTransform(pts1, pts2)
                        ROI = cv2.warpPerspective(image_data, M, (width, height))

                    nh, nw, nc = ROI.shape
                    # if nw /float(nh) > 5.:
                    #     cv2.imwrite('/data_sdd/crop/process_tax/crop_0104/vin_train/%s_%d.jpg' % (ID, count), ROI)
                    f.write('%d,%d,%d,%d,%d,%d,%d,%d,vin\n' % (x1, y1, x2, y2, x3, y3, x4, y4))
             '''
            draw_bboxes(image_data, bboxes_det, util.img.COLOR_RGB_RED)
            # print util.sit(pixel_score)
            # print util.sit(mask)

def main(_):
    dataset = config_initialization()
    test()
    
    
if __name__ == '__main__':
    tf.app.run()
