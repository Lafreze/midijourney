# encoding:utf-8
import requests
import os
import io
import json
import base64
import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common import const
from common.log import logger
from common.expired_dict import ExpiredDict
from plugins import *
from PIL import Image
from .mjapi import _mjApi
from .mjcache import _imgCache
from channel.chat_message import ChatMessage
from config import conf
from langdetect import detect
def check_prefix(content, prefix_list):
    if not prefix_list:
        return False, None
    for prefix in prefix_list:
        if content.startswith(prefix):
            return True, content.replace(prefix, "").strip()
    return False, None

def image_to_base64(image_path):
    filename, extension = os.path.splitext(image_path)
    t = extension[1:]
    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read())
        return f"data:image/{t};base64,{encoded_string.decode('utf-8')}"

def webp_to_png(webp_path):
    image_path = webp_path
    image_path = io.BytesIO()
    response = requests.get(webp_path)
    image = Image.open(io.BytesIO(response.content))
    image = image.convert("RGB")
    image.save(image_path, format="JPEG")
    return image_path

def read_file(path):
    with open(path, mode="r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=4)
    return True

@plugins.register(
    name="MidJourney",
    namecn="MJ绘画",
    desc="一款AI绘画工具",
    version="1.0.29",
    author="mouxan",
    desire_priority=0
)
class MidJourney(Plugin):
    def __init__(self):
        super().__init__()

        gconf = {
            "mj_url": "",
            "mj_api_secret": "",
            "imagine_prefix": "[\"/i\", \"/mj\", \"/imagine\", \"/img\"]",
            "fetch_prefix": "[\"/f\", \"/fetch\"]",
            "up_prefix": "[\"/u\", \"/up\"]",
            "pad_prefix": "[\"/p\", \"/pad\"]",
            "blend_prefix": "[\"/b\", \"/blend\"]",
            "describe_prefix": "[\"/d\", \"/describe\"]"
        }

        # 读取和写入配置文件
        curdir = os.path.dirname(__file__)
        config_path = os.path.join(curdir, "config.json")
        if not os.path.exists(config_path):
            config_path = os.path.join(curdir, "config.json.template")
        if os.environ.get("mj_url", None):
            logger.info("使用的是环境变量配置")
            try:
                if not self.mj_url or not self.mj_api_secret:
                    if not self.mj_url:
                        self.mj_url = conf().get("mj_url", "") 
                    if self.mj_url and not self.mj_api_secret:
                        self.mj_api_secret = conf().get("mj_api_secret", "") 
                    if not self.openai_api_key:
                        self.openai_api_key = conf().get("open_ai_api_key", "")
                self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
                logger.info("[MJ] inited. mj_url={} mj_api_secret={} imagine_prefix={} fetch_prefix={}".format(self.mj_url, self.mj_api_secret, self.imagine_prefix, self.fetch_prefix))
            except Exception as e:
                if isinstance(e, FileNotFoundError):
                    logger.warn(f"[MJ] init failed, config.json not found.")
                else:
                    logger.warn("[MJ] init failed." + str(e))
                raise e
            gconf = {
                "mj_url": os.environ.get("mj_url", ""),
                "mj_api_secret": os.environ.get("mj_api_secret", ""),
                "imagine_prefix": os.environ.get("imagine_prefix", "[\"/i\", \"/mj\", \"/imagine\", \"/img\"]"),
                "fetch_prefix": os.environ.get("fetch_prefix", "[\"/f\", \"/fetch\"]"),
                "up_prefix": os.environ.get("up_prefix", "[\"/u\", \"/up\"]"),
                "pad_prefix": os.environ.get("pad_prefix", "[\"/p\", \"/pad\"]"),
                "blend_prefix": os.environ.get("blend_prefix", "[\"/b\", \"/blend\"]"),
                "describe_prefix": os.environ.get("describe_prefix", "[\"/d\", \"/describe\"]")
            }
        else:
            logger.info(f"使用的是插件目录下的配置：{config_path}")
            gconf = {**gconf, **json.loads(read_file(config_path))}
        print(gconf)
        if gconf["mj_url"] == "":
            logger.info("[MJ] 未设置[mj_url]，请前往环境变量进行配置或在该插件目录下的config.json进行配置。")

        # 重新写入配置文件
        write_file(config_path, gconf)
        
        self.mj_url = gconf["mj_url"]
        self.mj_api_secret = gconf["mj_api_secret"]

        if not gconf["imagine_prefix"]:
            self.imagine_prefix = ["/mj", "/imagine", "/img"]
        else:
            self.imagine_prefix = eval(gconf["imagine_prefix"])
        if not gconf["fetch_prefix"]:
            self.fetch_prefix = ["/ft", "/fetch"]
        else:
            self.fetch_prefix = eval(gconf["fetch_prefix"])
        if not gconf["up_prefix"]:
            self.up_prefix = ["/u", "/up"]
        else:
            self.up_prefix = eval(gconf["up_prefix"])
        if not gconf["pad_prefix"]:
            self.pad_prefix = ["/p", "/pad"]
        else:
            self.pad_prefix = eval(gconf["pad_prefix"])
        if not gconf["blend_prefix"]:
            self.blend_prefix = ["/b", "/blend"]
        else:
            self.blend_prefix = eval(gconf["blend_prefix"])
        if not gconf["describe_prefix"]:
            self.describe_prefix = ["/d", "/describe"]
        else:
            self.describe_prefix = eval(gconf["describe_prefix"])
        
        # 目前没有设计session过期事件，这里先暂时使用过期字典
        if conf().get("expires_in_seconds"):
            self.sessions = ExpiredDict(conf().get("expires_in_seconds"))
        else:
            self.sessions = dict()
        
        self.mj = _mjApi(self.mj_url, self.mj_api_secret, self.imagine_prefix, self.fetch_prefix, self.up_prefix, self.pad_prefix, self.blend_prefix, self.describe_prefix)

        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

        logger.info("[MJ] inited. mj_url={} mj_api_secret={}".format(self.mj_url, self.mj_api_secret))
    def is_english(self, text):
        try:
            return detect(text) == 'en'
        except:
            return False
    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT,
            ContextType.IMAGE,
        ]:
            return

        channel = e_context['channel']
        context = e_context['context']
        content = context.content
        msg: ChatMessage = context["msg"]
        sessionid = context["session_id"]
        isgroup = e_context["context"].get("isgroup", False)
        reply = None

        # 图片
        if ContextType.IMAGE == context.type:
            # 需要调用准备函数下载图片，否则会出错
            msg.prepare()
            base64 = image_to_base64(content)
            img_cache = None
            if sessionid in self.sessions:
                img_cache = self.sessions[sessionid].get_cache()
            
            # 识别图片
            if (not isgroup and not img_cache) or (not isgroup and not img_cache["instruct"]) or (img_cache and img_cache["instruct"] == "describe"):
                self.env_detection(e_context)
                reply = self.describe(base64, channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
            
            if img_cache and img_cache["instruct"] == "imagine":
                self.env_detection(e_context)
                prompt = img_cache["prompt"]
                reply = self.imagine(prompt, base64, channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
            
            if img_cache and img_cache["instruct"] == "blend":
                self.env_detection(e_context)
                self.sessions[sessionid].action(base64)
                img_cache = self.sessions[sessionid].get_cache()
                length = len(img_cache["base64Array"])
                if length < 2:
                    reply = Reply(ReplyType.TEXT, f"✏  请再发送一张或多张图片")
                else:
                    reply = Reply(ReplyType.TEXT, f"✏  您已发送{length}张图片，可以发送更多图片或者发送[/end]开始合成")
            
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return


        if ContextType.TEXT == context.type:
            # 判断是否是指令
            iprefix, iq = check_prefix(content, self.imagine_prefix)
            fprefix, fq = check_prefix(content, self.fetch_prefix)
            uprefix, uq = check_prefix(content, self.up_prefix)
            pprefix, pq = check_prefix(content, self.pad_prefix)
            bprefix, bq = check_prefix(content, self.blend_prefix)
            dprefix, dq = check_prefix(content, self.describe_prefix)
            print(iq)
            last_match = ""
            if not self.is_english(iq) and iq is not None:
                try:
                    print("input:",iq)
                    if "--" in iq:
                        pattern_setting = iq.split("--")
                        for prompt in pattern_setting[1:]:
                            last_match += f"--{prompt}"
                        iq = pattern_setting[0]
                    print("prompt",iq)
                    message2 = f"""
                    As a prompt generator for a generative AI called "Midjourney", you will create image prompts for the AI to visualize. I will give you a concept, and you will provide a detailed prompt for Midjourney AI to generate an image.

                    Please adhere to the structure and formatting below, and follow these guidelines:

                    - Do not use the words "description" or ":" in any form.
                    - Do not place a comma between [ar] and [v].
                    - Write each prompt in one line without using return.
                    - Only provide the resulting text:.

                    Structure:
                    [1] = {iq}
                    [2] = a detailed description of [1] with specific imagery details.
                    [3] = a detailed description of the scene's environment.
                    [4] = a detailed description of the scene's mood, feelings, and atmosphere.
                    [5] = A style (e.g. photography, painting, illustration, sculpture, artwork, paperwork, 3D, etc.) for [1].
                    [6] = A description of how [5] will be executed (e.g. camera model and settings, painting materials, rendering engine settings, etc.)
                    [ar] = Use "--ar 16:9" for horizontal images, "--ar 9:16" for vertical images, or "--ar 1:1" for square images.
                    [v] = Use "--niji" for Japanese art style, or "--v 5" for other styles.

                    Formatting: 
                    Follow this prompt structure: "[1], [2], [3], [4], [5], [6], [ar] [v]".

                    Your task: Create only 1 prompt [1].

                    - Write your prompt in English.
                    - Do not describe unreal concepts as "real" or "photographic".
                    - Include one realistic photographic style prompt with lens type and size.

                    Example:
                    A stunning Halo Reach landscape with a Spartan on a hilltop, lush green forests surround them, clear sky, distant city view, focusing on the Spartan's majestic pose, intricate armor, and weapons, Artwork, oil painting on canvas, --ar 16:9 --v 5
                    """
                    message1 = f"Translate the following text delimited by 「」 to English and only provide the resulting text: 「{iq}」"
                    prompt = [{"role": "user", "content": message2}] if len(iq) < 10 else [{"role": "user", "content": message1}]
                    response = openai.ChatCompletion.create(
                                api_key=api_key, model=model,
                                    messages=prompt
                                )
                    print("translated:",response["choices"][0]["message"]["content"])
                    iq = response["choices"][0]["message"]["content"].replace("\"","").strip()
                    if len(last_match) > 0:
                        iq = f"{iq} {last_match}"
                    print("Final prompt:",iq)
                    print('#tokens:',response['usage']['total_tokens'])
                except Exception as e:
                    print(e)
            if content == "/mjhp" or content == "/mjhelp" or content == "/mj-help":
                reply = Reply(ReplyType.INFO, self.mj.help_text())
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
                return
            elif iprefix == True:
                self.env_detection(e_context)
                reply = self.imagine(iq, "", channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif uprefix == True:
                self.env_detection(e_context)
                reply = self.up(uq, channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif pprefix == True:
                self.env_detection(e_context)
                if not pq:
                    reply = Reply(ReplyType.TEXT, "✨ 垫图模式\n✏ 请在指令后输入要绘制的描述文字")
                else:
                    self.sessions[sessionid] = _imgCache(sessionid, "imagine", pq)
                    reply = Reply(ReplyType.TEXT, "✨ 垫图模式\n✏ 请再发送一张图片")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif bprefix == True:
                self.env_detection(e_context)
                self.sessions[sessionid] = _imgCache(sessionid, "blend", bq)
                reply = Reply(ReplyType.TEXT, "✨ 混图模式\n✏ 请发送两张或多张图片，然后输入['/end']结束")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif dprefix == True:
                self.env_detection(e_context)
                self.sessions[sessionid] = _imgCache(sessionid, "describe", dq)
                reply = Reply(ReplyType.TEXT, "✨ 识图模式\n✏ 请发送一张图片")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif content.startswith("/re"):
                self.env_detection(e_context)
                id = content.replace("/re", "").strip()
                reply = self.reroll(id, channel, context)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif content == "/end":
                self.env_detection(e_context)
                # 从会话中获取缓存的图片
                img_cache = None
                if sessionid in self.sessions:
                    img_cache = self.sessions[sessionid].get_cache()
                if not img_cache:
                    reply = Reply(ReplyType.TEXT, "请先输入指令开启绘图模式")
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return
                base64Array = img_cache["base64Array"]
                prompt = img_cache["prompt"]
                length = len(base64Array)
                if length >= 2:
                    reply = self.blend(img_cache["base64Array"], prompt, channel, context)
                    if sessionid in self.sessions:
                        self.sessions[sessionid].reset()
                        del self.sessions[sessionid]
                elif length == 0:
                    reply = Reply(ReplyType.TEXT, "✨ 混图模式\n✏ 请发送两张或多张图片方可完成混图")
                else:
                    reply = Reply(ReplyType.TEXT, "✨ 混图模式\n✏ 请再发送一张图片方可完成混图")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif fprefix == True:
                self.env_detection(e_context)
                logger.debug("[MJ] /fetch id={}".format(fq))
                status, msg, imageUrl = self.mj.fetch(fq)
                if status:
                    if imageUrl:
                        self.sendMsg(msg, channel, context)
                        image_path = webp_to_png(imageUrl)
                        reply = Reply(ReplyType.IMAGE, image_path)
                    else:
                        reply = Reply(ReplyType.TEXT, msg)
                else:
                    reply = Reply(ReplyType.ERROR, msg)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            elif content == "/queue":
                self.env_detection(e_context)
                status, msg = self.mj.task_queue()
                if status:
                    reply = Reply(ReplyType.TEXT, msg)
                else:
                    reply = Reply(ReplyType.ERROR, msg)
                if sessionid in self.sessions:
                    self.sessions[sessionid].reset()
                    del self.sessions[sessionid]
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return

    def get_help_text(self, **kwargs):
        if kwargs.get("verbose") != True:
            return "这是一个AI绘画工具，只要输入想到的文字，通过人工智能产出相对应的图。"
        else:
            return self.mj.help_text()
    
    def env_detection(self, e_context: EventContext):
        if not self.mj_url:
            reply = Reply(ReplyType.ERROR, "未设置[mj_url]，请前往环境变量进行配置或在该插件目录下的config.json进行配置。")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return
    
    def imagine(self, prompt, base64, channel, context):
        logger.debug("[MJ] /imagine prompt={} img={}".format(prompt, base64))
        reply = None
        status, msg, id = self.mj.imagine(prompt, base64)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def up(self, id, channel, context):
        logger.debug("[MJ] /up id={}".format(id))
        reply = None
        status, msg, id = self.mj.simpleChange(id)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def describe(self, base64, channel, context):
        logger.debug("[MJ] /describe img={}".format(base64))
        reply = None
        status, msg, id = self.mj.describe(base64)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def blend(self, base64Array, dimensions, channel, context):
        logger.debug("[MJ] /blend imgList={} dimensions={}".format(base64Array, dimensions))
        reply = None
        status, msg, id = self.mj.blend(base64Array, dimensions)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def reroll(self, id, channel, context):
        logger.debug("[MJ] /reroll id={}".format(id))
        reply = None
        status, msg, id = self.mj.reroll(id)
        if status:
            self.sendMsg(msg, channel, context)
            reply = self.get_f_img(id, channel, context)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def get_f_img(self, id, channel, context):
        status2, msg, imageUrl = self.mj.get_f_img(id)
        if status2:
            if imageUrl:
                self.sendMsg(msg, channel, context)
                image_path = webp_to_png(imageUrl)
                reply = Reply(ReplyType.IMAGE, image_path)
            else:
                reply = Reply(ReplyType.TEXT, msg)
        else:
            reply = Reply(ReplyType.ERROR, msg)
        return reply
    
    def sendMsg(self, msg, channel, context, types=ReplyType.TEXT):
        return channel._send_reply(context, channel._decorate_reply(context, Reply(types, msg)))


def check_prefix(content, prefix_list):
    prefix_list = eval(prefix_list)
    if not prefix_list:
        return False, None
    for prefix in prefix_list:
        if content.startswith(prefix):
            logger.info("[MJ] prefix={} content={} test={}".format(prefix, content, content.startswith(prefix)))
            return True, content.replace(prefix, "").strip()
    return False, None
def image_to_base64(image_path):
    filename, extension = os.path.splitext(image_path)
    t = extension[1:]
    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read())
        return f"data:image/{t};base64,{encoded_string.decode('utf-8')}"

def webp_to_png(webp_path):
    image_path = webp_path
    if(".webp" in webp_path):
        image_path = io.BytesIO()
        response = requests.get(webp_path)
        image = Image.open(io.BytesIO(response.content))
        image = image.convert("RGB")
        image.save(image_path, format="JPEG")
        return ReplyType.IMAGE, image_path
    else:
        return ReplyType.IMAGE_URL, image_path

class MidJourney2(Plugin):
    def __init__(self):
        super().__init__()
        self.mj_url = os.environ.get("mj_url", None)
        self.mj_api_secret = os.environ.get("mj_api_secret", None)
        self.imagine_prefix = os.environ.get("imagine_prefix", "[\"/imagine\", \"/mj\", \"/img\"]")
        self.fetch_prefix = os.environ.get("fetch_prefix", "[\"/fetch\", \"/ft\"]")
        self.openai_api_key = os.environ.get("openai_api_key", None)
        self.reply_img = conf().get("mj_reply_img", False)
        try:
            if not self.mj_url or not self.mj_api_secret:
                if not self.mj_url:
                    self.mj_url = conf().get("mj_url", "") 
                if self.mj_url and not self.mj_api_secret:
                    self.mj_api_secret = conf().get("mj_api_secret", "") 
                if not self.openai_api_key:
                    self.openai_api_key = conf().get("open_ai_api_key", "")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            logger.info("[MJ] inited. mj_url={} mj_api_secret={} imagine_prefix={} fetch_prefix={}".format(self.mj_url, self.mj_api_secret, self.imagine_prefix, self.fetch_prefix))
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                logger.warn(f"[MJ] init failed, config.json not found.")
            else:
                logger.warn("[MJ] init failed." + str(e))
            raise e
    def is_english(self, text):
        try:
            return detect(text) == 'en'
        except:
            return False
    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT,
        ]:
            return
        
        mj = _mjApi(self.mj_url, self.mj_api_secret)

        channel = e_context['channel']
        context = e_context['context']
        content = context.content
        api_key = self.openai_api_key
        model = "gpt-3.5-turbo-0613"
        admin_data = conf().get_user_data(channel.user_id)
        admin_data["mj_reply_img"] = admin_data.get("mj_reply_img", conf().get("mj_reply_img"))
        self.reply_img = admin_data["mj_reply_img"]
        if content == "/mjhp" or content == "/mjhelp" or content == "/mj-help":
            reply = Reply(ReplyType.INFO, mj.help_text())
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束,并跳过处理context的默认逻辑
            return
        iprefix, iq = check_prefix(content, self.imagine_prefix)
        print(iq)
        img_url = ""
        last_match = ""
        if not self.is_english(iq) and iq is not None:
            try:
                print("input:",iq)
                if ">" in iq:
                    pattern_url = "<([^>]+)>"
                    match = re.search(pattern_url, iq)
                    if match:
                        img_url = match.group(1)
                    iq = re.sub(pattern_url, "", iq)
                if "--" in iq:
                    pattern_setting = iq.split("--")
                    for prompt in pattern_setting[1:]:
                        last_match += f"--{prompt}"
                    iq = pattern_setting[0]
                print("prompt",iq)
                message2 = f"""
                As a prompt generator for a generative AI called "Midjourney", you will create image prompts for the AI to visualize. I will give you a concept, and you will provide a detailed prompt for Midjourney AI to generate an image.

                Please adhere to the structure and formatting below, and follow these guidelines:

                - Do not use the words "description" or ":" in any form.
                - Do not place a comma between [ar] and [v].
                - Write each prompt in one line without using return.
                - Only provide the resulting text:.

                Structure:
                [1] = {iq}
                [2] = a detailed description of [1] with specific imagery details.
                [3] = a detailed description of the scene's environment.
                [4] = a detailed description of the scene's mood, feelings, and atmosphere.
                [5] = A style (e.g. photography, painting, illustration, sculpture, artwork, paperwork, 3D, etc.) for [1].
                [6] = A description of how [5] will be executed (e.g. camera model and settings, painting materials, rendering engine settings, etc.)
                [ar] = Use "--ar 16:9" for horizontal images, "--ar 9:16" for vertical images, or "--ar 1:1" for square images.
                [v] = Use "--niji" for Japanese art style, or "--v 5" for other styles.

                Formatting: 
                Follow this prompt structure: "[1], [2], [3], [4], [5], [6], [ar] [v]".

                Your task: Create only 1 prompt [1].

                - Write your prompt in English.
                - Do not describe unreal concepts as "real" or "photographic".
                - Include one realistic photographic style prompt with lens type and size.

                Example:
                A stunning Halo Reach landscape with a Spartan on a hilltop, lush green forests surround them, clear sky, distant city view, focusing on the Spartan's majestic pose, intricate armor, and weapons, Artwork, oil painting on canvas, --ar 16:9 --v 5
                """
                message1 = f"Translate the following text delimited by 「」 to English and only provide the resulting text: 「{iq}」"
                prompt = [{"role": "user", "content": message2}] if len(iq) < 10 else [{"role": "user", "content": message1}]
                response = openai.ChatCompletion.create(
                            api_key=api_key, model=model,
                                messages=prompt
                            )
                print("translated:",response["choices"][0]["message"]["content"])
                iq = response["choices"][0]["message"]["content"].replace("\"","").strip()
                if len(img_url) > 0:
                    iq = f"{img_url} {iq}"
                if len(last_match) > 0:
                    iq = f"{iq} {last_match}"
                print("Final prompt:",iq)
                print('#tokens:',response['usage']['total_tokens'])
            except Exception as e:
                print(e)
        
        if iprefix == True or content.startswith("/up"):
            logger.info("[MJ] iprefix={} iq={}".format(iprefix,iq))
            reply = None
            if iprefix == True:
                status, msg, id = mj.imagine(iq)
            else:
                status, msg, id = mj.simpleChange(content.replace("/up", "").strip())
            if status:
                self.sendMsg(channel, context, ReplyType.TEXT, msg)
                status2, msgs, imageUrl = mj.get_f_img(id, self.reply_img)
                if status2:
                    self.sendMsg(channel, context, ReplyType.TEXT, msgs)
                    if self.reply_img:
                        reply_type, image_path = webp_to_png(imageUrl)
                        reply = Reply(reply_type, image_path)
                else:
                    reply = Reply(ReplyType.ERROR, msgs)
            else:
                reply = Reply(ReplyType.ERROR, msg)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return
        
        fprefix, fq = check_prefix(content, self.fetch_prefix)
        if fprefix == True:
            logger.info("[MJ] fprefix={} fq={}".format(fprefix,fq))
            status, msg, imageUrl = mj.fetch(fq)
            reply = None
            if status:
                self.sendMsg(channel, context, ReplyType.TEXT, msg)
                if imageUrl and self.reply_img:
                    reply_type, image_path = webp_to_png(imageUrl)
                    reply = Reply(reply_type, image_path)
            else:
                reply = Reply(ReplyType.ERROR, msg)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            return


    def get_help_text(self, isadmin=False, isgroup=False, verbose=False,**kwargs):
        if kwargs.get("verbose") != True:
            return "这是一个使用MidiJourney模型的绘画工具,只要输入想到的文字,即可产出相对应的图。"
        else:
            return _mjApi().help_text()
    
    def sendMsg(self, channel, context, types, msg):
        return channel._send_reply(context, channel._decorate_reply(context, Reply(types, msg)))
        


class _mjApi:
    def __init__(self, mj_url, mj_api_secret):
        self.baseUrl = mj_url
        self.headers = {
            "Content-Type": "application/json",
        }
        if mj_api_secret:
            self.headers["mj-api-secret"] = mj_api_secret
    
    def imagine(self, text):
        try:
            url = self.baseUrl + "/mj/submit/imagine"
            data = {"prompt": text}
            res = requests.post(url, json=data, headers=self.headers)
            code = res.json()["code"]
            if code == 1:
                msg = f"✅ 您的任务已提交\n"
                msg += f"🚀 正在处理中,请稍等\n"
                msg += f"📨 任务ID: {res.json()['result']}\n"
                msg += f"🪄 查询进度\n"
                msg += f"✏  使用[/fetch + 任务ID操作]\n"
                msg += f"/fetch {res.json()['result']}"
                return True, msg, str(res.json()["result"])
            else:
                return False, str(res.json()["description"]), False
        except Exception as e:
            return False, str(e), False
    
    def simpleChange(self, content):
        try:
            url = self.baseUrl + "/mj/submit/simple-change"
            data = {"content": content}
            res = requests.post(url, json=data, headers=self.headers)
            code = res.json()["code"]
            if code == 1:
                msg = f"✅ 您的任务已提交\n"
                msg += f"🚀 正在处理中,请稍后\n"
                msg += f"📨 任务ID: {res.json()['result']}\n"
                msg += f"🪄 查询进度\n"
                msg += f"✏  使用[/fetch + 任务ID操作]\n"
                msg += f"/fetch {res.json()['result']}"
                return True, msg, res.json()["result"]
            else:
                return False, res.json()["description"], False
        except Exception as e:
            return False, str(e), False
    
    def fetch(self, id):
        try:
            url = self.baseUrl + f"/mj/task/{id}/fetch"
            res = requests.get(url, headers=self.headers)
            status = res.json()['status']
            startTime = ""
            finishTime = ""
            if res.json()['startTime']:
                startTime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(res.json()['startTime']/1000))
            if res.json()['finishTime']:
                finishTime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(res.json()['finishTime']/1000))
            msg = "✅ 查询成功\n"
            msg += f"任务ID: {res.json()['id']}\n"
            msg += f"描述内容:{res.json()['prompt']}\n"
            msg += f"状态:{self.status(status)}\n"
            msg += f"进度:{res.json()['progress']}\n"
            if startTime:
                msg += f"开始时间:{startTime}\n"
            if finishTime:
                msg += f"完成时间:{finishTime}\n"
            if res.json()['imageUrl']:
                return True, msg, res.json()['imageUrl']
            return True, msg, False
        except Exception as e:
            return False, str(e), False
    
    def status(self, status):
        msg = ""
        if status == "SUCCESS":
            msg = "已成功"
        elif status == "FAILURE":
            msg = "失败"
        elif status == "SUBMITTED":
            msg = "已提交"
        elif status == "IN_PROGRESS":
            msg = "处理中"
        else:
            msg = "未知"
        return msg
    
    def get_f_img(self, id, reply_img):
        try:
            url = self.baseUrl + f"/mj/task/{id}/fetch"
            status = ""
            rj = ""
            counter = 0
            while status != "SUCCESS" :
                time.sleep(5)
                res = requests.get(url, headers=self.headers)
                rj = res.json()
                status = rj["status"]
                if status == "SUBMITTED":
                    counter += 1
                    if counter > 20:
                        print(status)
                        break
                
            action = rj["action"]
            msg = ""
            startTime = ""
            finishTime = ""
            if res.json()['startTime']:
                startTime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(res.json()['startTime']/1000))
            if res.json()['finishTime']:
                finishTime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(res.json()['finishTime']/1000))
            print(action)
            if action == "IMAGINE":
                print("status:",status)
                if status == "SUCCESS":
                    msg = f"🌞 绘图成功\n"
                    msg += f"✨ 内容: {rj['prompt']}\n"
                    msg += f"📨 任务ID: {id}\n"
                    msg += f"⚙️ 放大 U1～U4,变换 V1～V4\n"
                    msg += f"📝 使用[/up 任务ID 操作]\n"
                    msg += f"/up {id} U1\n"
                    if startTime:
                        msg += f"⏱开始时间:{startTime}\n"
                    if finishTime:
                        msg += f"⏱完成时间:{finishTime}\n"
                elif status == "SUBMITTED":
                    msg = f"🌚 您的任务后台处理中\n"
                    msg += f"🌟 请稍后联系我索要图片\n"
                    msg += f"✨ 内容: {rj['prompt']}\n"
                    if startTime:
                        msg += f"⏱开始时间:{startTime}\n"
                    if finishTime:
                        msg += f"⏱完成时间:{finishTime}\n"
                else:
                    msg = f"😔 您的任务已丢失\n"
                    msg += f"✨ 内容: {rj['prompt']}\n"
                    if startTime:
                        msg += f"⏱开始时间:{startTime}\n"
                    if finishTime:
                        msg += f"⏱完成时间:{finishTime}\n"
            elif action == "UPSCALE":
                msg = "🎨 放大成功\n"
                msg += f"✨ {rj['description']}\n"
            else:
                msg = f"🌞 变换成功\n"
                msg += f"📨 任务ID: {id}\n"
                msg += f"⚙️ 放大 U1～U4,变换 V1～V4\n"
                msg += f"📝 使用[/up 任务ID 操作]\n"
                msg += f"/up {id} U1\n"
                if startTime:
                    msg += f"⏱开始时间:{startTime}\n"
                if finishTime:
                    msg += f"⏱完成时间:{finishTime}\n"
            if not reply_img and rj["imageUrl"]:
                msg += f'🔍点击查看:\n{rj["imageUrl"]}'
            return True, msg, rj["imageUrl"]
        except Exception as e:
            return False, str(e), False
    
    def help_text(self):
        help_text = "欢迎使用MidiJourney for WeChat\n"
        help_text += f"这是一个AI绘画工具,只要输入想到的文字,通过MidiJourney产出相对应的图。\n"
        help_text += f"------------------------------\n"
        help_text += f"🎨 AI绘图-使用说明:\n"
        help_text += f"输入: /imagine prompt\n"
        help_text += f"prompt 即你提的绘画需求\n"
        help_text += f"------------------------------\n"
        help_text += f"📕 prompt附加参数 \n"
        help_text += f"1.解释: 在prompt后携带的参数, 可以使你的绘画更别具一格\n"
        help_text += f"2.示例: /imagine prompt --ar 16:9\n"
        help_text += f"3.使用: 需要使用--key value, key和value空格隔开, 多个附加参数空格隔开\n"
        help_text += f"------------------------------\n"
        help_text += f"📗 附加参数列表\n"
        help_text += f"1. --v 版本 1,2,3,4,5, 5.2 默认5, 不可与niji同用\n"
        help_text += f"2. --niji 卡通版本 空或5 默认空, 不可与v同用\n"
        help_text += f"3. --ar 横纵比 n:n 默认1:1\n"
        help_text += f"4. --q 清晰度 .25 .5 1 2 分别代表: 一般,清晰,高清,超高清,默认1\n"
        help_text += f"5. --style 风格 (4a,4b,4c)v4可用 (expressive,cute)niji5可用\n"
        help_text += f"6. --s 风格化 1-1000 (625-60000)v3"
        help_text += f"7. --iw 近似度 (0-2) 越大越靠近输入的图片"
        return help_text
