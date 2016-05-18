#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Trains, evaluates and saves the model network using a queue."""
# pylint: disable=missing-docstring
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import imp
import json
import logging
import numpy as np
import os.path
import sys

# configure logging
if 'TV_IS_DEV' in os.environ and os.environ['TV_IS_DEV']:
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        level=logging.INFO,
                        stream=sys.stdout)
else:
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        level=logging.INFO,
                        stream=sys.stdout)


import time

from shutil import copyfile

from six.moves import xrange  # pylint: disable=redefined-builtin

import tensorflow as tf

import tensorvision.utils as utils
import tensorvision.core as core

flags = tf.app.flags
FLAGS = flags.FLAGS


def _copy_parameters_to_traindir(hypes, input_file, target_name, target_dir):
    """
    Helper to copy files defining the network to the saving dir.

    Parameters
    ----------
    input_file : str
        name of source file
    target_name : str
        target name
    traindir : str
        directory where training data is saved
    """
    target_file = os.path.join(target_dir, target_name)
    input_file = os.path.join(hypes['dirs']['base_path'], input_file)
    copyfile(input_file, target_file)


def _start_enqueuing_threads(hypes, q, sess, data_input):
    """Start the enqueuing threads of the data_input module.

    Parameters
    ----------
    hypes : dict
        Hyperparameters
    sess : session
    q : queue
    data_input: data_input
    """
    with tf.name_scope('data_load'):
            data_input.start_enqueuing_threads(hypes, q['train'], 'train',
                                               sess, hypes['dirs']['data_dir'])
            data_input.start_enqueuing_threads(hypes, q['val'], 'val', sess,
                                               hypes['dirs']['data_dir'])


def initialize_training_folder(hypes):
    """
    Creating the training folder and copy all model files into it.

    The model will be executed from the training folder and all
    outputs will be saved there.

    Parameters
    ----------
    hypes : dict
        Hyperparameters
    """
    target_dir = os.path.join(hypes['dirs']['output_dir'], "model_files")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    # Creating an additional logging saving the console outputs
    # into the training folder
    logging_file = os.path.join(hypes['dirs']['output_dir'], "output.log")
    filewriter = logging.FileHandler(logging_file, mode='w')
    formatter = logging.Formatter(
        '%(asctime)s %(name)-3s %(levelname)-3s %(message)s')
    filewriter.setLevel(logging.INFO)
    filewriter.setFormatter(formatter)
    logging.getLogger('').addHandler(filewriter)

    # TODO: read more about loggers and make file logging neater.

    hypes_file = os.path.basename(tf.app.flags.FLAGS.hypes)
    _copy_parameters_to_traindir(
        hypes, hypes_file, "hypes.json", target_dir)
    _copy_parameters_to_traindir(
        hypes, hypes['model']['input_file'], "data_input.py", target_dir)
    _copy_parameters_to_traindir(
        hypes, hypes['model']['architecture_file'], "architecture.py",
        target_dir)
    _copy_parameters_to_traindir(
        hypes, hypes['model']['objective_file'], "objective.py", target_dir)
    _copy_parameters_to_traindir(
        hypes, hypes['model']['optimizer_file'], "solver.py", target_dir)


def maybe_download_and_extract(hypes):
    """
    Download the data if it isn't downloaded by now.

    Parameters
    ----------
    hypes : dict
        Hyperparameters
    """
    f = os.path.join(hypes['dirs']['base_path'], hypes['model']['input_file'])
    data_input = imp.load_source("input", f)
    if hasattr(data_input, 'maybe_download_and_extract'):
        data_input.maybe_download_and_extract(hypes, hypes['dirs']['data_dir'])


def _write_precision_to_summary(precision, summary_writer, name, global_step,
                                sess):
    """
    Write the precision to the summary file.

    Parameters
    ----------
    precision : tensor
    summary_writer : tf.train.SummaryWriter
    name : string
        Name of Operation to write
    global_step : tensor or int
        Xurrent training step
    sess : tf.Session
    """
    # write result to summary
    summary = tf.Summary()
    # summary.ParseFromString(sess.run(summary_op))
    summary.value.add(tag='Evaluation/' + name + ' Precision',
                      simple_value=precision)
    summary_writer.add_summary(summary, global_step)


        The modules load in utils
def _print_training_status(hypes, step, loss_value, start_time, sess_coll):
    duration = (time.time() - start_time) / int(utils.cfg.step_show)
    examples_per_sec = hypes['solver']['batch_size'] / duration
    sec_per_batch = float(duration)
    info_str = utils.cfg.step_str

    sess, saver, summary_op, summary_writer, coord, threads = sess_coll
    logging.info(info_str.format(step=step,
                                 total_steps=hypes['solver']['max_steps'],
                                 loss_value=loss_value,
                                 sec_per_batch=sec_per_batch,
                                 examples_per_sec=examples_per_sec)
                 )
    # Update the events file.
    summary_str = sess.run(summary_op)
    summary_writer.add_summary(summary_str, step)


def _write_checkpoint_to_disk(hypes, step, sess_coll):
    sess, saver, summary_op, summary_writer, coord, threads = sess_coll
    checkpoint_path = os.path.join(hypes['dirs']['output_dir'],
                                   'model.ckpt')
    saver.save(sess, checkpoint_path, global_step=step)


def _do_evaluation(hypes, step, sess_coll, eval_dict):
    sess, saver, summary_op, summary_writer, coord, threads = sess_coll
    logging.info('Doing Evaluate with Training Data.')

    precision = core.do_eval(hypes, eval_dict, phase='train',
                             sess=sess)
    _write_precision_to_summary(precision, summary_writer,
                                "Train", step, sess)

    logging.info('Doing Evaluation with Testing Data.')
    precision = core.do_eval(hypes, eval_dict, phase='val',
                             sess=sess)
    _write_precision_to_summary(precision, summary_writer,
                                'val', step, sess)


def run_training_step(hypes, step, start_time, graph_ops, sess_coll):
    """Run one iteration of training."""
    # Unpack operations for later use
    sess = sess_coll[0]
    q, train_op, loss, eval_dict = graph_ops

    # Run the training Step
    _, loss_value = sess.run([train_op, loss])

    # Write the summaries and print an overview fairly often.
    if step % int(utils.cfg.step_show) == 0:
        # Print status to stdout.
        _print_training_status(hypes, step, loss_value, start_time, sess_coll)
        # Reset timer
        start_time = time.time()

    # Save a checkpoint and evaluate the model periodically.
    if (step + 1) % int(utils.cfg.step_eval) == 0 or \
       (step + 1) == hypes['solver']['max_steps']:
        # write checkpoint to disk
        _write_checkpoint_to_disk(hypes, step, sess_coll)
        # Reset timer
        start_time = time.time()

    # Do a evaluation and print the current state
    if (step + 1) % int(utils.cfg.step_eval) == 0 or \
       (step + 1) == hypes['solver']['max_steps']:
        # write checkpoint to disk
        _do_evaluation(hypes, step, sess_coll, eval_dict)
        # Reset timer
        start_time = time.time()

    return start_time


def do_training(hypes):
    """
    Train model for a number of steps.

    This trains the model for at most hypes['solver']['max_steps'].
    It shows an update every utils.cfg.step_show steps and writes
    the model to hypes['dirs']['output_dir'] every utils.cfg.step_eval
    steps.

    Paramters
    ---------
    hypes : dict
        Hyperparameters
    """
    # Get the sets of images and labels for training, validation, and
    # test on MNIST.

    modules = utils.load_modules_from_hypes(hypes)
    data_input, arch, objective, solver = modules

    # Tell TensorFlow that the model will be built into the default Graph.
    with tf.Graph().as_default():

        # build the graph based on the loaded modules
        graph_ops = core.build_graph(hypes, modules)
        q = graph_ops[0]

        # prepaire the tv session
        sess_coll = core.start_tv_session(hypes)
        sess, saver, summary_op, summary_writer, coord, threads = sess_coll

        # Start the data load
        _start_enqueuing_threads(hypes, q, sess, data_input)

        # And then after everything is built, start the training loop.
        start_time = time.time()
        for step in xrange(hypes['solver']['max_steps']):
            start_time = run_training_step(hypes, step, start_time,
                                           graph_ops, sess_coll)

        # stopping input Threads
        coord.request_stop()
        coord.join(threads)


def main(_):
    """Run main function."""
    if FLAGS.hypes is None:
        logging.error("No hypes are given.")
        logging.error("Usage: tv-train --hypes hypes.json")
        exit(1)

    if FLAGS.gpus is None:
        if 'TV_USE_GPUS' in os.environ:
            if os.environ['TV_USE_GPUS'] == 'force':
                logging.error('Please specify a GPU.')
                logging.error('Usage tv-train --gpus <ids>')
                exit(1)
            else:
                gpus = os.environ['TV_USE_GPUS']
                logging.info("GPUs are set to: %s", gpus)
                os.environ['CUDA_VISIBLE_DEVICES'] = gpus
    else:
        logging.info("GPUs are set to: %s", FLAGS.gpus)
        os.environ['CUDA_VISIBLE_DEVICES'] = FLAGS.gpus

    with open(tf.app.flags.FLAGS.hypes, 'r') as f:
        logging.info("f: %s", f)
        hypes = json.load(f)

    utils.load_plugins()
    utils.set_dirs(hypes, tf.app.flags.FLAGS.hypes)

    logging.info("Initialize Training Folder")
    initialize_training_folder(hypes)
    maybe_download_and_extract(hypes)
    logging.info("Start Training")
    do_training(hypes)


if __name__ == '__main__':
    tf.app.run()
