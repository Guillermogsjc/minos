'''
Created on Feb 6, 2017

@author: julien
'''
from copy import deepcopy
import logging
import traceback

import keras
from keras.engine.topology import Input, Merge
from keras.engine.training import Model
from keras.layers.core import Dense
from keras.regularizers import L1L2Regularizer

from minos.model.parameters import is_custom_activation, get_custom_activation,\
    is_custom_layer, get_custom_layer
from minos.train.utils import cpu_device, is_gpu_device, get_logical_device


class ModelBuilder(object):

    def __init__(self):
        pass

    def build(self, blueprint, device, compile_model=True):
        model = _build_model(blueprint, device)
        if compile_model:
            model.compile(
                optimizer=_build_optimizer(blueprint.training),
                loss=blueprint.training.objective.objective,
                metrics=[blueprint.training.metric.metric])
        return model


def _build_model(blueprint, device):
    if isinstance(device, list):
        return _build_multi_device_model(blueprint, device)
    else:
        return _build_single_device_model(blueprint, device)


def _build_single_device_model(blueprint, device):
    import tensorflow as tf
    with tf.device(get_logical_device(device)):
        inputs = Input(shape=(blueprint.layout.input_size,))
        row_input = inputs
        for row in blueprint.layout.rows:
            row_input = _build_row_model(row_input, row)
        final_layer_input = _maybe_merge_inputs(row_input)
        predictions = Dense(
            blueprint.layout.output_size,
            activation=blueprint.layout.output_activation)(final_layer_input)
        return Model(input=inputs, output=predictions)


def _build_multi_device_model(blueprint, devices):
    import tensorflow as tf
    model = _build_single_device_model(blueprint, cpu_device())
    gpu_devices = [d for d in devices if is_gpu_device(d)]
    inputs = tf.unstack(model.inputs)
    device_batch_size = len(inputs) // len(gpu_devices)
    inputs = [
        tf.stack(inputs[i:i + device_batch_size], axis=0)
        for i, _ in enumerate(gpu_devices)]
    outputs = []
    for i, device in enumerate(gpu_devices):
        with tf.device(device):
            outputs.append(model(inputs[i]))
    with tf.device(cpu_device()):
        output = tf.concat(outputs, 0)
        return Model(input=model.inputs, output=output)


def _build_row_model(inputs, row):
    return [
        _build_block_model(inputs, block)
        for block in row.blocks]


def _build_block_model(inputs, block):
    if isinstance(inputs, list) and len(inputs) == 1:
        inputs = inputs[0]
    if block.input_layers and len(block.input_layers) > 0:
        for layer in block.input_layers:
            inputs = _build_layer_model(inputs, layer)
    for layer in block.layers:
        inputs = _build_layer_model(inputs, layer)
    return inputs


def _maybe_merge_inputs(inputs):
    if isinstance(inputs, list) and len(inputs) > 1:
        return Merge(mode='concat')(inputs)
    elif isinstance(inputs, list) and len(inputs) == 1:
        return inputs[0]
    else:
        return inputs


def _build_layer_model(inputs, layer):
    try:
        parameters = _build_layer_parameters(layer)
        model = _get_layer_model(layer.layer_type)
        return model(**parameters)(inputs)
    except Exception as ex:
        logging.debug(traceback.format_exc())
        raise ex


def _get_layer_model(layer_type):
    if is_custom_layer(layer_type):
        return get_custom_layer(layer_type)[0]
    modules = [keras.layers, keras.layers.normalization]
    for module in modules:
        model = getattr(module, layer_type)
        if model:
            return model
    return None


def _build_layer_parameters(layer):
    parameters = deepcopy(layer.parameters)
    regularizers = [
        'activity_regularizer',
        'b_regularizer',
        'W_regularizer',
        'gamma_regularizer',
        'beta_regularizer']
    for regularizer in regularizers:
        if regularizer in parameters:
            parameters[regularizer] = _get_regularizer(parameters[regularizer])
    activation = parameters.get('activation', None)
    if activation:
        if is_custom_activation(activation):
            parameters['activation'] = get_custom_activation(activation)
    return parameters


def _get_regularizer(regularizer_parameter):
    if regularizer_parameter is None\
            or len(regularizer_parameter) == 0\
            or all(value is None for _, value in regularizer_parameter.items()):
        return None
    l1 = regularizer_parameter.get('l1', 0.)
    l2 = regularizer_parameter.get('l2', 0.)
    return L1L2Regularizer(l1, l2)


def _build_optimizer(training):
    optimizer = getattr(keras.optimizers, training.optimizer.optimizer)
    return optimizer(**training.optimizer.parameters)
