import json
import numpy as np
import torch
import Models
from transformers import BertForSequenceClassification
from transformers import AutoTokenizer
from sklearn.metrics import f1_score, accuracy_score


def tag_to_index(x):
    if x == 'bokmål':
        return 0
    elif x == 'nynorsk':
        return 1
    elif x == 'dialectal':
        return 2
    else:
        return 3


with open('Data/dialect_classification/dialect_tweet_train.json', 'r', encoding="utf-8") as data:
    polarity_array = json.load(data)
    train_texts = [datapoint['text'] for datapoint in polarity_array]
    train_labels = [tag_to_index(datapoint['category']) for datapoint in polarity_array]

with open('Data/dialect_classification/dialect_tweet_dev.json', 'r', encoding="utf-8") as data:
    polarity_array = json.load(data)
    val_texts = [datapoint['text'] for datapoint in polarity_array]
    val_labels = [tag_to_index(datapoint['category']) for datapoint in polarity_array]

with open('Data/dialect_classification/dialect_tweet_test.json', 'r', encoding="utf-8") as data:
    polarity_array = json.load(data)
    test_texts = [datapoint['text'] for datapoint in polarity_array]
    test_labels = [tag_to_index(datapoint['category']) for datapoint in polarity_array]

""" Parse data into datasets """

nb_bert_pipe = Models.get_nb_bert(4, model_type=BertForSequenceClassification)
mbert_pipe = Models.get_mbert(4, model_type=BertForSequenceClassification)
nor_bert_pipe = Models.get_nor_bert(4, model_type=BertForSequenceClassification)

nor_bert_tokenizer = AutoTokenizer.from_pretrained("ltgoslo/norbert")
nb_bert_tokenizer = AutoTokenizer.from_pretrained('NbAiLab/nb-bert-base')
mbert_tokenizer = AutoTokenizer.from_pretrained('bert-base-multilingual-cased')

nor_bert_train_encodings = nor_bert_tokenizer(train_texts, truncation=True, padding=True)
nb_bert_train_encodings = nb_bert_tokenizer(train_texts, truncation=True, padding=True)
mbert_train_encodings = mbert_tokenizer(train_texts, truncation=True, padding=True)

nor_bert_val_encodings = nor_bert_tokenizer(val_texts, truncation=True, padding=True)
nb_bert_val_encodings = nb_bert_tokenizer(val_texts, truncation=True, padding=True)
mbert_val_encodings = mbert_tokenizer(val_texts, truncation=True, padding=True)

nor_bert_test_encodings = nor_bert_tokenizer(test_texts, truncation=True, padding=True)
nb_bert_test_encodings = nb_bert_tokenizer(test_texts, truncation=True, padding=True)
mbert_test_encodings = mbert_tokenizer(test_texts, truncation=True, padding=True)


class SentinentPolarityDataset(torch.utils.data.Dataset):
    def __init__(self, encodings, labels):
        self.encodings = encodings
        self.labels = labels

    def __getitem__(self, idx):
        item = {key: torch.tensor(val[idx]) for key, val in self.encodings.items()}
        item['labels'] = torch.tensor(self.labels[idx])
        return item

    def __len__(self):
        return len(self.labels)


nor_bert_train_dataset = SentinentPolarityDataset(nor_bert_train_encodings, train_labels)
nb_bert_train_dataset = SentinentPolarityDataset(nb_bert_train_encodings, train_labels)
mbert_train_dataset = SentinentPolarityDataset(mbert_train_encodings, train_labels)

nor_bert_test_dataset = SentinentPolarityDataset(nor_bert_test_encodings, test_labels)
nb_bert_test_dataset = SentinentPolarityDataset(nb_bert_test_encodings, test_labels)
mbert_test_dataset = SentinentPolarityDataset(mbert_test_encodings, test_labels)

""" Tune models """
from torch.utils.data import DataLoader
from transformers import AdamW
from tqdm import tqdm

device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')


def tune(model, optim, dataset):
    loader = DataLoader(dataset, batch_size=16, shuffle=True)
    model.train()
    for epoch in range(32):
        for batch in tqdm(loader):
            optim.zero_grad()
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            labels = batch['labels'].to(device)
            outputs = model(input_ids, attention_mask=attention_mask, labels=labels)
            loss = outputs[0]
            loss.backward()
            optim.step()
    model.eval()


tag_values = ['bokmål', 'nynorsk', 'dialekt', 'mixed']


def eval(model, dataset):
    loader = DataLoader(dataset, batch_size=8, shuffle=True)
    model.eval()
    eval_loss, eval_accuracy = 0, 0
    predictions, true_labels = [], []
    testing_loss_values = []
    for batch in tqdm(loader):
        input_ids = batch['input_ids'].to(device)
        attention_mask = batch['attention_mask'].to(device)
        labels = batch['labels'].to(device)

        with torch.no_grad():
            outputs = model(input_ids, attention_mask=attention_mask, labels=labels)
        # Move logits and labels to CPU
        logits = outputs[1].detach().cpu().numpy()
        label_ids = labels.to('cpu').numpy()

        # Calculate the accuracy for this batch of test sentences.
        eval_loss += outputs[0].mean().item()

        

        predictions.extend(np.argmax(logits, axis=1))
        true_labels.extend(label_ids)

    print(eval_loss)
    eval_loss = eval_loss / len(loader)
    testing_loss_values.append(eval_loss)
    print(f"Model scores")
    print("testing loss: {}".format(eval_loss))
    pred_tags = []
    test_tags = []
    for p, l in zip(predictions, true_labels):
        curr_p = []
        curr_l = []
        curr_p.append(tag_values[p])
        curr_l.append(tag_values[l])
        pred_tags.append(curr_p)
        test_tags.append(curr_l)

    print("testing Accuracy: {}".format(accuracy_score(pred_tags, test_tags)))
    print("testing F1-Score: {}".format(f1_score(pred_tags, test_tags, average='macro')))
    return f1_score(pred_tags, test_tags, average='macro'), accuracy_score(pred_tags, test_tags)


def get_optimizer_grouped_param(model):
    param_optimizer = list(model.named_parameters())
    no_decay = ['bias', 'gamma', 'beta']
    optimizer_grouped_parameters = [
        {'params': [p for n, p in param_optimizer if not any(nd in n for nd in no_decay)],
         'weight_decay_rate': 0.01},
        {'params': [p for n, p in param_optimizer if any(nd in n for nd in no_decay)],
         'weight_decay_rate': 0.0}
    ]
    return optimizer_grouped_parameters

nor_bert_model = nor_bert_pipe.model
nor_bert_model.to(device)
nor_bert_optim = AdamW(get_optimizer_grouped_param(nor_bert_model), lr=5e-5)
tune(nor_bert_model, nor_bert_optim, nor_bert_train_dataset)
nor_bert_f1, nor_bert_accuracy = eval(nor_bert_model, nor_bert_test_dataset)

nb_bert_model = nb_bert_pipe.model
nb_bert_model.to(device)

nb_bert_optim = AdamW(get_optimizer_grouped_param(nb_bert_model), lr=1e-5)
tune(nb_bert_model, nb_bert_optim, nb_bert_train_dataset)
nb_bert_f1, nb_bert_accuracy = eval(nb_bert_model, nb_bert_test_dataset)

mbert_model = mbert_pipe.model
mbert_model.to(device)
mbert_optim = AdamW(get_optimizer_grouped_param(mbert_model), lr=5e-5)
tune(mbert_model, mbert_optim, mbert_train_dataset)
mbert_f1, mbert_accuracy = eval(mbert_model, mbert_test_dataset)

with open('./results/dialect_classification.txt', 'w') as file:
    file.write('NorBert - F1 score: ' + str(nor_bert_f1) + ' Accuracy: ' + str(nor_bert_accuracy) + '\n')
    file.write('NbBert - F1 score: ' + str(nb_bert_f1) + ' Accuracy: ' + str(nb_bert_accuracy) + '\n')
    file.write('mBert - F1 score: ' + str(mbert_f1) + ' Accuracy: ' + str(mbert_accuracy) + '\n')
