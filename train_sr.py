import torch
from datasets import load_from_disk, load_metric
from dataclasses import dataclass
from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC,TrainingArguments, Trainer
from typing import Dict, List, Union
import numpy as np


# Model Settings
MODEL = "facebook/wav2vec2-xls-r-300m"
REPO_NAME = "xls-r-300m-es"


# Trainer API
def train_model():

    # Loading datasets
    print("Loading train and test datasets ...")
    common_voice_train = load_from_disk("/opt/volumen1/files/hf_challenge/datasets/train")
    common_voice_test = load_from_disk("/opt/volumen1/files/hf_challenge/datasets/test")
    print("---Datasets ready!---")

    # Loading model and processor
    processor = Wav2Vec2Processor.from_pretrained("polodealvarado/xls-r-300m-es")
    model = Wav2Vec2ForCTC.from_pretrained(
        MODEL, 
        attention_dropout=0.0,
        hidden_dropout=0.0,
        feat_proj_dropout=0.0,
        mask_time_prob=0.05,
        layerdrop=0.0,
        ctc_loss_reduction="mean", 
        pad_token_id=processor.tokenizer.pad_token_id,
        vocab_size=len(processor.tokenizer),
    )
    # Freeze CNN layers that are used to extract acoustically meaningful features from the raw speech signal
    model.freeze_feature_extractor()
    device = torch.device('cuda:0')
    model.to(device)
    print("---Model device: {0}---".format(model.device))


    # Data Settings
    @dataclass
    class DataCollatorCTCWithPadding:
        """
        Data collator that will dynamically pad the inputs received.
        Args:
            processor (:class:`~transformers.Wav2Vec2Processor`)
                The processor used for proccessing the data.
            padding (:obj:`bool`, :obj:`str` or :class:`~transformers.tokenization_utils_base.PaddingStrategy`, `optional`, defaults to :obj:`True`):
                Select a strategy to pad the returned sequences (according to the model's padding side and padding index)
                among:
                * :obj:`True` or :obj:`'longest'`: Pad to the longest sequence in the batch (or no padding if only a single
                sequence if provided).
                * :obj:`'max_length'`: Pad to a maximum length specified with the argument :obj:`max_length` or to the
                maximum acceptable input length for the model if that argument is not provided.
                * :obj:`False` or :obj:`'do_not_pad'` (default): No padding (i.e., can output a batch with sequences of
                different lengths).
        """

        processor: Wav2Vec2Processor
        padding: Union[bool, str] = True

        def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
            # split inputs and labels since they have to be of different lenghts and need
            # different padding methods
            input_features = [{"input_values": feature["input_values"]} for feature in features]
            label_features = [{"input_ids": feature["labels"]} for feature in features]

            batch = self.processor.pad(
                input_features,
                padding=self.padding,
                return_tensors="pt",
            )
            with self.processor.as_target_processor():
                labels_batch = self.processor.pad(
                    label_features,
                    padding=self.padding,
                    return_tensors="pt",
                )

            # replace padding with -100 to ignore loss correctly
            labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)

            batch["labels"] = labels

            return batch

    data_collator = DataCollatorCTCWithPadding(processor=processor, padding=True)



    # Metrics
    wer_metric = load_metric("wer")
    cer_metric = load_metric("cer")
    def compute_metrics(pred):
        pred_logits = pred.predictions
        pred_ids = np.argmax(pred_logits, axis=-1)

        pred.label_ids[pred.label_ids == -100] = processor.tokenizer.pad_token_id

        pred_str = processor.batch_decode(pred_ids)
        # we do not want to group tokens when computing the metrics
        label_str = processor.batch_decode(pred.label_ids, group_tokens=False)

        wer = wer_metric.compute(predictions=pred_str, references=label_str)
        cer = cer_metric.compute(predictions=pred_str, references=label_str)

        return {"wer": wer,"cer":cer}


    # Trainer API
    training_args = TrainingArguments(
    output_dir=REPO_NAME,
    group_by_length=True,
    per_device_train_batch_size=16,
    gradient_accumulation_steps=3,
    evaluation_strategy="steps",
    num_train_epochs=15,
    gradient_checkpointing=True,
    fp16=True,
    save_steps=1000,
    eval_steps=1000,
    logging_steps=1000,
    learning_rate=5e-4,
    warmup_steps=500,
    save_total_limit=2,
    push_to_hub=False,
    )

    trainer = Trainer(
        model=model,
        data_collator=data_collator,
        args=training_args,
        compute_metrics=compute_metrics,
        train_dataset=common_voice_train,
        eval_dataset=common_voice_test,
        tokenizer=processor.feature_extractor,
    )


    trainer.train()


    # Save Logs:
    with ("logs.txt","w") as log_file:
        log_file.write(trainer.state.log_history)

if __name__ == "__main__":
    train_model()