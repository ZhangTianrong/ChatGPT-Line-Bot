import os
from paperqa import Docs
from langchain.chat_models import ChatOpenAI
from langchain.vectorstores import FAISS
from langchain.embeddings.openai import OpenAIEmbeddings

class PDF:
    def get_pdf_link(self, url:str):
        if (url.startswith("https://arxiv.org/abs/")):
            url = url.replace("/abs/","/pdf/")
            return url
        if (url.startswith("https://arxiv.org/pdf/")):
            return url
        return None

class PDFQA(Docs):
    def __init__(
            self, chunk_size_limit= 3000, llm = None, summary_llm = None, name = "default", index_path = None, model_name = "gpt-3.5-turbo",
            openai_api_key=""
        ):
        self.openai_api_key = openai_api_key
        super().__init__(chunk_size_limit, llm, summary_llm, name, index_path, model_name)

    def update_llm(self, llm, summary_llm = None):
        if llm is None:
            llm = "gpt-3.5-turbo"
        if type(llm) is str:
            llm = ChatOpenAI(temperature=0.1, model=llm, openai_api_key=self.openai_api_key)
        if type(summary_llm) is str:
            summary_llm = ChatOpenAI(temperature=0.1, model=summary_llm, openai_api_key=self.openai_api_key)
        return super().update_llm(llm, summary_llm)

    def __setstate__(self, state):
        self.__dict__.update(state)
        try:
            self._faiss_index = FAISS.load_local(self.index_path, OpenAIEmbeddings(openai_api_key=self.openai_api_key))
        except:
            # they use some special exception type, but I don't want to import it
            self._faiss_index = None
        self.update_llm("gpt-3.5-turbo")

    def query(self, query, k = 10, max_sources = 5, length_prompt = "about 100 words", marginal_relevance = True):
        os.environ["OPENAI_API_KEY"] = self.openai_api_key
        ans = super().query(query, k, max_sources, length_prompt, marginal_relevance)
        os.environ.pop("OPENAI_API_KEY")
        return ans