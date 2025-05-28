import sys
import threading
from pathlib import Path
import time
import json
import os
import yt_dlp
from urllib.parse import urlparse
import platform
import subprocess
import socket
import tempfile
import random

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QProgressBar, QScrollArea, QFrame,
    QMessageBox, QSizePolicy, QCheckBox, QLineEdit,
    QGroupBox, QInputDialog, QButtonGroup, QRadioButton
)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QTimer, QMetaObject, Q_ARG

from server import DLNAServer
from mpv_controller import MPVController, MPVDLNARenderer, get_base_path, set_mpv_default_path

# 下载状态信号类
class DownloadSignals(QObject):
    progress = Signal(str, float, float, int)  # download_id, progress, speed, eta
    finished = Signal(str, str)  # download_id, file_path
    error = Signal(str, str)  # download_id, error_message
    status = Signal(str, str)  # download_id, status_message

# 下载管理器
class DownloadManager(QObject):
    def __init__(self):
        super().__init__()
        self.signals = DownloadSignals()
        self.downloads = {}
        self.active_downloads = {}  # 存储活动的下载任务
        
    def download_video(self, url, title, download_id):
        """下载视频"""
        try:
            # 创建下载目录
            download_dir = Path("downloads")
            download_dir.mkdir(exist_ok=True)
            
            # 清理文件名
            safe_title = "".join(c for c in title if c.isalnum() or c in ('-', '_', '.'))
            safe_title = safe_title.strip()
            
            # 记录最大进度
            max_progress = 0
            last_update_time = 0
            
            def progress_hook(d):
                nonlocal max_progress, last_update_time
                if d['status'] == 'downloading':
                    # 检查是否应该停止下载
                    if download_id in self.active_downloads and not self.active_downloads[download_id]:
                        raise Exception("下载已停止")
                    
                    # 控制更新频率
                    current_time = time.time()
                    if current_time - last_update_time < 1.0:  # 每秒最多更新一次
                        return
                    last_update_time = current_time
                        
                    # 计算进度
                    progress = 0
                    downloaded_bytes = d.get('downloaded_bytes', 0)
                    if downloaded_bytes is None:
                        downloaded_bytes = 0
                        
                    if 'total_bytes' in d and d['total_bytes'] is not None and d['total_bytes'] > 0:
                        progress = downloaded_bytes / d['total_bytes']
                    elif 'total_bytes_estimate' in d and d['total_bytes_estimate'] is not None and d['total_bytes_estimate'] > 0:
                        progress = downloaded_bytes / d['total_bytes_estimate']
                    
                    # 确保进度只增不减
                    if progress > max_progress:
                        max_progress = progress
                    else:
                        progress = max_progress
                    
                    # 计算速度,避免None值
                    speed = d.get('speed', 0)
                    if speed is None:
                        speed = 0
                    speed = speed / 1024 / 1024  # MB/s
                    
                    # 获取预计剩余时间,避免None值
                    eta = d.get('eta', 0)
                    if eta is None:
                        eta = 0
                    
                    # 发送进度信号
                    self.signals.progress.emit(download_id, progress, speed, eta)
                
                elif d['status'] == 'finished':
                    # 检查是否应该停止下载
                    if download_id in self.active_downloads and not self.active_downloads[download_id]:
                        raise Exception("下载已停止")
                    self.signals.status.emit(download_id, '处理中...')
            
            # 设置下载选项
            ydl_opts = {
                'format': 'best',
                'outtmpl': str(download_dir / f'{safe_title}.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'ignoreerrors': True,
                'progress_hooks': [progress_hook]
            }
            
            # 标记下载任务为活动状态
            self.active_downloads[download_id] = True
            
            # 开始下载
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            # 检查是否应该停止下载
            if download_id in self.active_downloads and not self.active_downloads[download_id]:
                raise Exception("下载已停止")
            
            # 查找下载的文件
            downloaded_files = list(download_dir.glob(f"{safe_title}.*"))
            if not downloaded_files:
                raise Exception("下载完成但未找到文件")
            
            # 发送完成信号
            self.signals.finished.emit(download_id, str(downloaded_files[0]))
            
        except Exception as e:
            # 发送错误信号
            self.signals.error.emit(download_id, str(e))
        finally:
            # 清理下载任务状态
            if download_id in self.active_downloads:
                del self.active_downloads[download_id]
    
    def stop_download(self, download_id):
        """停止下载"""
        if download_id in self.active_downloads:
            self.active_downloads[download_id] = False
            self.signals.status.emit(download_id, '正在停止下载...')
            # 发送错误信号，这样UI可以重置状态
            self.signals.error.emit(download_id, "下载已停止")

# 添加URL处理函数
def truncate_url(url, max_length=50):
    """截断URL显示"""
    if len(url) <= max_length:
        return url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/...{parsed.path[-20:]}"

# 下载项目组件
class DownloadItem(QFrame):
    def __init__(self, download_id, title, url, parent=None):
        super().__init__(parent)
        self.download_id = download_id
        self.title = title
        self.url = url
        self.downloaded_file_path = None
        self.setup_ui()
        self.check_existing_file()  # 添加检查现有文件
        
    def setup_ui(self):
        # 设置Frame样式
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        
        # 主布局
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(6)

        # 信息区域
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        # 标题
        self.title_label = QLabel(self.title)
        self.title_label.setWordWrap(True)
        info_layout.addWidget(self.title_label)

        # URL
        self.url_label = QLabel(truncate_url(self.url))
        self.url_label.setToolTip(self.url)
        info_layout.addWidget(self.url_label)

        main_layout.addLayout(info_layout)

        # 操作按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        # 下载控制按钮
        self.download_btn = QPushButton("下载")
        self.download_btn.clicked.connect(self.start_download)
        btn_layout.addWidget(self.download_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self.stop_download)
        self.stop_btn.setVisible(False)
        btn_layout.addWidget(self.stop_btn)

        # 添加预览按钮
        self.preview_btn = QPushButton("预览")
        self.preview_btn.clicked.connect(self.preview_media)
        btn_layout.addWidget(self.preview_btn)

        # 复制按钮
        self.copy_btn = QPushButton("复制")
        self.copy_btn.clicked.connect(self.copy_url)
        btn_layout.addWidget(self.copy_btn)

        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        # 底部状态区域
        self.status_layout = QVBoxLayout()
        self.status_layout.setSpacing(4)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setVisible(False)
        self.status_layout.addWidget(self.progress_bar)

        # 状态标签
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        self.status_layout.addWidget(self.status_label)

        main_layout.addLayout(self.status_layout)

    def check_existing_file(self):
        """检查文件是否已经下载"""
        try:
            download_dir = Path("downloads")
            if not download_dir.exists():
                return
                
            # 生成安全的文件名（与下载时使用相同的逻辑）
            safe_title = "".join(c for c in self.title if c.isalnum() or c in ('-', '_', '.'))
            safe_title = safe_title.strip()
            
            # 检查所有可能的文件扩展名
            for file_path in download_dir.glob(f"{safe_title}.*"):
                if file_path.is_file():
                    self.downloaded_file_path = str(file_path)
                    self.download_btn.setText("打开文件")
                    self.download_btn.clicked.disconnect()
                    self.download_btn.clicked.connect(self.open_file)
                    self.status_label.setText(f"文件已存在：{file_path.name}")
                    self.status_label.setVisible(True)
                    break
        except Exception as e:
            print(f"检查文件是否存在时出错: {e}")

    def preview_media(self):
        """预览媒体，使用设置的默认播放器"""
        try:
            # 使用DLNA渲染器播放
            if window.mpv_dlna_renderer:
                # 设置标题（必须在播放前设置，这样在播放时才能同步给DLNA）
                window.mpv_dlna_renderer.set_media_title(self.title)
                
                # 播放媒体
                if window.mpv_dlna_renderer.set_media_url(self.url):
                    self.status_label.setText("✅ 已开始预览")
                    self.status_label.setVisible(True)
                else:
                    self.status_label.setText("❌ 播放器启动失败")
                    self.status_label.setVisible(True)
            else:
                self.status_label.setText("❌ 播放器未初始化")
                self.status_label.setVisible(True)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"预览失败: {str(e)}")
            self.status_label.setText(f"❌ 预览失败: {str(e)}")
            self.status_label.setVisible(True)

    def start_download(self):
        """开始下载或打开已存在的文件"""
        if self.downloaded_file_path and Path(self.downloaded_file_path).exists():
            reply = QMessageBox.question(
                self,
                "文件已存在",
                "该文件已经下载，是否重新下载？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.No:
                self.open_file()
                return
                
        # 开始新的下载
        self.downloaded_file_path = None  # 重置文件路径
        self.download_btn.setVisible(False)
        self.stop_btn.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("准备下载...")
        self.status_label.setVisible(True)
        
        # 启动下载线程
        download_thread = threading.Thread(
            target=window.download_manager.download_video,
            args=(self.url, self.title, self.download_id)
        )
        download_thread.start()
    
    def stop_download(self):
        self.stop_btn.setVisible(False)
        self.download_btn.setVisible(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("下载已停止")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        window.download_manager.stop_download(self.download_id)

    def update_progress(self, progress, speed, eta):
        """更新下载进度"""
        # 只在进度条可见时更新
        if self.progress_bar.isVisible():
            # 确保进度只增不减
            current_value = self.progress_bar.value()
            new_value = int(progress * 100)
            if new_value < current_value:
                new_value = current_value
            else:
                self.progress_bar.setValue(new_value)
            
            speed_text = f"{speed:.1f} MB/s" if speed > 0 else "计算中..."
            eta_text = f"{eta // 60:02d}:{eta % 60:02d}" if eta > 0 else "计算中..."
            self.status_label.setText(f"进度：{new_value}%  ▏  速度：{speed_text}  ▏  剩余时间：{eta_text}")
            self.status_label.setVisible(True)
            
            # 避免频繁调整大小
            if not hasattr(self, '_last_adjust_time'):
                self._last_adjust_time = 0
            current_time = time.time()
            if current_time - self._last_adjust_time > 0.5:  # 每0.5秒最多调整一次大小
                self.adjustSize()
                self._last_adjust_time = current_time

    def download_finished(self, file_path):
        """下载完成"""
        self.downloaded_file_path = file_path
        self.progress_bar.setValue(100)
        self.status_label.setText(f"✅ 下载完成：{Path(file_path).name}")
        self.download_btn.setText("打开文件")
        self.download_btn.setVisible(True)
        self.download_btn.clicked.disconnect()  # 断开原有的下载信号连接
        self.download_btn.clicked.connect(self.open_file)  # 连接到打开文件功能
        self.stop_btn.setVisible(False)
        self.adjustSize()

    def download_error(self, error_msg):
        """下载错误"""
        self.status_label.setText(f"❌ {error_msg}")
        self.status_label.setStyleSheet("color: #e74c3c;")
        self.progress_bar.setVisible(False)
        self.download_btn.setVisible(True)
        self.stop_btn.setVisible(False)
        self.adjustSize()

    def update_status(self, status):
        """更新状态"""
        self.status_label.setText(status)
        self.status_label.setVisible(True)
        self.adjustSize()

    def open_file(self):
        """打开文件或文件所在目录"""
        if not self.downloaded_file_path or not Path(self.downloaded_file_path).exists():
            QMessageBox.warning(self, "错误", "文件不存在")
            return
            
        try:
            # 获取默认播放器设置
            default_player = 'mpv'
            try:
                if os.path.exists('settings.json'):
                    with open('settings.json', 'r', encoding='utf-8') as f:
                        settings = json.load(f)
                        default_player = settings.get('default_player', 'mpv')
            except Exception as e:
                print(f"读取默认播放器设置失败: {e}")
            
            # 弹出选择对话框
            options = ["打开文件所在目录", "使用MPV播放"]
            
            # 根据系统添加其他选项
            system = platform.system()
            if system == "Darwin":
                options.append("使用IINA播放")
            elif system == "Windows":
                options.append("使用PotPlayer播放")
            
            # 设置默认选中项
            default_index = 0
            if default_player == 'mpv':
                default_index = 1
            elif default_player == 'iina' and system == "Darwin":
                default_index = 2
            elif default_player == 'potplayer' and system == "Windows":
                default_index = 2
            
            # 显示选择对话框
            action, ok = QInputDialog.getItem(
                self, 
                "选择操作", 
                "请选择要执行的操作:", 
                options, 
                default_index, 
                False
            )
            
            if not ok:
                return
                
            # 根据选择执行操作
            if action == "打开文件所在目录":
                if system == "Darwin":  # macOS
                    os.system(f'open -R "{self.downloaded_file_path}"')
                elif system == "Windows":  # Windows
                    os.system(f'explorer /select,"{self.downloaded_file_path}"')
            elif action == "使用MPV播放":
                if window.mpv_controller:
                    window.mpv_controller.start_mpv(self.downloaded_file_path)
                else:
                    mpv_path = set_mpv_default_path()
                    mpv_args = [
                        mpv_path,
                        '--force-window=yes',
                        '--ontop',
                        '--keep-open=yes',
                        self.downloaded_file_path
                    ]
                    subprocess.Popen(mpv_args)
            elif action == "使用IINA播放" and system == "Darwin":
                if window.iina_controller:
                    window.iina_controller.start_iina(self.downloaded_file_path)
                else:
                    os.system(f'open -a IINA "{self.downloaded_file_path}"')
            elif action == "使用PotPlayer播放" and system == "Windows":
                os.startfile(f'potplayer://"{self.downloaded_file_path}"')
                
        except Exception as e:
            QMessageBox.warning(self, "错误", f"操作失败: {str(e)}")

    def copy_url(self):
        """复制链接到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.url)
        self.status_label.setText("✅ 链接已复制到剪贴板")
        # 3秒后清除状态
        QTimer.singleShot(3000, lambda: self.status_label.setText(""))

# 主窗口
class MainWindow(QMainWindow):
    # 添加自定义信号
    update_button_state = Signal(bool, str)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DLNA投屏助手")
        self.setMinimumSize(800, 600)  # 设置最小尺寸
        self.resize(800, 600)  # 设置默认尺寸
        
        # 创建MPV控制器
        self.mpv_controller = MPVController()
        
        # 在macOS上创建IINA控制器
        self.iina_controller = None
        if platform.system() == "Darwin":
            from mpv_controller import IINAController
            self.iina_controller = IINAController()
        
        # 创建下载管理器
        self.download_manager = DownloadManager()
        self.setup_signals()
        
        # 创建DLNA服务器
        self.dlna_server = DLNAServer(name="龙龙的电视机")
        # 添加自定义的MPV渲染器
        self.mpv_dlna_renderer = MPVDLNARenderer(self.dlna_server)
        # 设置MPV控制器
        self.mpv_dlna_renderer.set_mpv_controller(self.mpv_controller)
        self.mpv_controller.dlna_server = self.dlna_server
        
        # 设置IINA控制器（如果存在）
        if self.iina_controller:
            self.mpv_dlna_renderer.set_iina_controller(self.iina_controller)
            self.iina_controller.dlna_server = self.dlna_server
        
        # 添加投屏回调
        self.dlna_server.add_cast_callback(self.on_new_cast)
        # 在后台线程启动服务器
        self.server_thread = None
        self.server_running = False
        
        # 设置主界面
        self.setup_ui()
        
        # 加载历史记录
        self.load_history()
        
        # 加载自动播放设置
        self.load_auto_play_setting()
        
        # 启动服务器
        self.start_server()
        
        # 连接信号到槽
        self.update_button_state.connect(self._update_button_state)
        
        # 关联MPV控制器信号
        self.mpv_controller.mpv_connection_error.connect(self.on_mpv_connection_error)
    
    def setup_signals(self):
        self.download_manager.signals.progress.connect(self.handle_download_progress)
        self.download_manager.signals.finished.connect(self.handle_download_finished)
        self.download_manager.signals.error.connect(self.handle_download_error)
        self.download_manager.signals.status.connect(self.handle_status_update)
    
    def setup_ui(self):
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        
        # 服务器状态组
        status_group = QGroupBox("服务器状态")
        status_layout = QHBoxLayout(status_group)
        
        # 服务器状态图标和文本
        status_container = QWidget()
        status_info_layout = QHBoxLayout(status_container)
        status_info_layout.setContentsMargins(0, 0, 0, 0)
        
        self.status_icon = QLabel("🖥️")
        status_info_layout.addWidget(self.status_icon)
        
        self.status_text = QLabel("DLNA服务器运行中")
        status_info_layout.addWidget(self.status_text)
        status_info_layout.addStretch()
        
        status_layout.addWidget(status_container)
        
        # 设备名称编辑区域
        name_container = QWidget()
        name_layout = QHBoxLayout(name_container)
        name_layout.setContentsMargins(0, 0, 0, 0)
        
        self.device_name_edit = QLineEdit("龙龙的电视机")
        self.device_name_edit.setPlaceholderText("输入设备名称")
        self.device_name_edit.setMinimumWidth(150)
        name_layout.addWidget(self.device_name_edit)
        
        self.update_name_btn = QPushButton("修改名称")
        self.update_name_btn.clicked.connect(self.update_device_name)
        name_layout.addWidget(self.update_name_btn)
        
        status_layout.addWidget(name_container)
        
        # 服务器控制按钮
        control_container = QWidget()
        control_layout = QHBoxLayout(control_container)
        control_layout.setContentsMargins(0, 0, 0, 0)
        
        self.start_server_btn = QPushButton("启动服务器")
        self.start_server_btn.clicked.connect(self.start_server)
        control_layout.addWidget(self.start_server_btn)
        
        self.stop_server_btn = QPushButton("停止服务器")
        self.stop_server_btn.clicked.connect(self.stop_server)
        self.stop_server_btn.setVisible(False)
        control_layout.addWidget(self.stop_server_btn)
        
        status_layout.addWidget(control_container)
        main_layout.addWidget(status_group)
        
        # 创建滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        # 创建滚动区域的内容部件
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setSpacing(8)
        self.content_layout.addStretch()
        
        scroll.setWidget(self.content_widget)
        main_layout.addWidget(scroll)
        
        # 使用说明组
        help_group = QGroupBox("使用说明")
        help_layout = QVBoxLayout(help_group)
        help_text = """1. 确保手机和电脑在同一网络
2. 打开视频APP，点击投屏按钮
3. 选择"龙龙的电视机"即可开始投屏"""
        help_label = QLabel(help_text)
        help_layout.addWidget(help_label)
        main_layout.addWidget(help_group)
        
        # 底部控制区域
        bottom_group = QGroupBox()
        bottom_layout = QHBoxLayout(bottom_group)
        
        # 第一列：自动播放选项
        first_column = QWidget()
        first_column_layout = QVBoxLayout(first_column)
        first_column_layout.setContentsMargins(0, 0, 0, 0)
        
        self.auto_play_checkbox = QCheckBox("收到投屏自动打开播放器")
        self.auto_play_checkbox.stateChanged.connect(self.on_auto_play_changed)
        first_column_layout.addWidget(self.auto_play_checkbox)

        self.auto_download_checkbox = QCheckBox("投屏后自动下载")
        self.auto_download_checkbox.stateChanged.connect(self.on_auto_download_changed)
        first_column_layout.addWidget(self.auto_download_checkbox)
        
        # 添加全屏选项
        self.fullscreen_checkbox = QCheckBox("全屏播放")
        self.fullscreen_checkbox.stateChanged.connect(self.on_fullscreen_changed)
        first_column_layout.addWidget(self.fullscreen_checkbox)
        
        bottom_layout.addWidget(first_column)
        
        # 第二列：默认播放器选择
        second_column = QWidget()
        second_column_layout = QVBoxLayout(second_column)
        second_column_layout.setContentsMargins(0, 0, 0, 0)
        
        # 默认播放器选项
        player_label = QLabel("默认播放器:")
        second_column_layout.addWidget(player_label)
        
        player_container = QWidget()
        player_layout = QHBoxLayout(player_container)
        player_layout.setContentsMargins(0, 0, 0, 0)
        
        # 创建单选按钮组
        self.player_group = QButtonGroup(self)
        
        # 添加MPV选项
        self.mpv_radio = QRadioButton("MPV")
        self.mpv_radio.clicked.connect(lambda: self.on_default_player_changed("mpv"))
        self.player_group.addButton(self.mpv_radio)
        player_layout.addWidget(self.mpv_radio)
        
        # 根据平台添加其他播放器选项
        system = platform.system()
        if system == "Darwin":
            self.iina_radio = QRadioButton("IINA")
            self.iina_radio.clicked.connect(lambda: self.on_default_player_changed("iina"))
            self.player_group.addButton(self.iina_radio)
            player_layout.addWidget(self.iina_radio)
        elif system == "Windows":
            self.potplayer_radio = QRadioButton("PotPlayer")
            self.potplayer_radio.clicked.connect(lambda: self.on_default_player_changed("potplayer"))
            self.player_group.addButton(self.potplayer_radio)
            player_layout.addWidget(self.potplayer_radio)
        
        second_column_layout.addWidget(player_container)
        bottom_layout.addWidget(second_column)
        
        # 第三列：操作按钮
        third_column = QWidget()
        third_column_layout = QHBoxLayout(third_column)
        third_column_layout.setContentsMargins(0, 0, 0, 0)
        
        # 添加打开下载文件夹按钮
        open_downloads_btn = QPushButton("打开下载文件夹")
        open_downloads_btn.clicked.connect(self.open_downloads_folder)
        third_column_layout.addWidget(open_downloads_btn)
        
        clear_btn = QPushButton("清空记录")
        clear_btn.clicked.connect(self.clear_history)
        third_column_layout.addWidget(clear_btn)
        
        bottom_layout.addWidget(third_column)
        bottom_layout.addStretch()
        
        main_layout.addWidget(bottom_group)
    
    def load_history(self):
        try:
            if os.path.exists('cast_history.json'):
                with open('cast_history.json', 'r', encoding='utf-8') as f:
                    history = json.load(f)
                    for item in history:
                        self.add_download_item(item['url'], item['title'])
        except Exception as e:
            QMessageBox.warning(self, "错误", f"加载历史记录失败: {str(e)}")
    
    def add_download_item(self, url, title):
        download_id = f"download_{int(time.time() * 1000)}"
        item = DownloadItem(download_id, title, url)
        self.content_layout.insertWidget(0, item)
        return item
    
    @Slot(str, float, float, int)
    def handle_download_progress(self, download_id, progress, speed, eta):
        for i in range(self.content_layout.count()):
            item = self.content_layout.itemAt(i).widget()
            if isinstance(item, DownloadItem) and item.download_id == download_id:
                item.update_progress(progress, speed, eta)
                break
    
    @Slot(str, str)
    def handle_download_finished(self, download_id, file_path):
        for i in range(self.content_layout.count()):
            item = self.content_layout.itemAt(i).widget()
            if isinstance(item, DownloadItem) and item.download_id == download_id:
                item.download_finished(file_path)
                break
    
    @Slot(str, str)
    def handle_download_error(self, download_id, error_msg):
        for i in range(self.content_layout.count()):
            item = self.content_layout.itemAt(i).widget()
            if isinstance(item, DownloadItem) and item.download_id == download_id:
                item.download_error(error_msg)
                break
    
    @Slot(str, str)
    def handle_status_update(self, download_id, status):
        for i in range(self.content_layout.count()):
            item = self.content_layout.itemAt(i).widget()
            if isinstance(item, DownloadItem) and item.download_id == download_id:
                item.update_status(status)
                break
    
    def on_new_cast(self, url, title):
        """处理新的投屏"""
        # 使用Qt的信号机制在主线程中更新UI
        QMetaObject.invokeMethod(self, "add_new_cast",
                               Qt.QueuedConnection,
                               Q_ARG(str, url),
                               Q_ARG(str, title))
    
    @Slot(str, str)
    def add_new_cast(self, url, title):
        """在主线程中添加新的投屏记录"""
        # 添加到界面
        item = self.add_download_item(url, title)
        
        # 如果启用了自动播放，则自动打开播放器
        if self.auto_play_checkbox.isChecked():
            # 设置标题（必须在播放前设置，这样在播放时才能同步给DLNA）
            self.mpv_dlna_renderer.set_media_title(title)
            
            # 使用DLNA渲染器播放，它会根据设置选择正确的播放器
            if self.mpv_dlna_renderer.set_media_url(url):
                print(f"自动打开播放器成功: {title}")
            else:
                print(f"自动打开播放器失败: {title}")

        # 如果启用了自动下载，则模拟点击下载按钮
        if self.auto_download_checkbox.isChecked():
            # 使用 QTimer 延迟一小段时间后再触发下载
            # 这样可以确保界面完全初始化
            QTimer.singleShot(500, lambda: item.download_btn.click())
        
        # 保存到历史记录
        try:
            history = []
            if os.path.exists('cast_history.json'):
                with open('cast_history.json', 'r', encoding='utf-8') as f:
                    history = json.load(f)
            
            # 添加新记录
            history.append({
                'url': url,
                'title': title,
                'timestamp': time.strftime("%Y-%m-%d %H:%M:%S")
            })
            
            # 限制历史记录数量
            if len(history) > 100:
                history = history[-100:]
            
            # 保存历史记录
            with open('cast_history.json', 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"保存历史记录失败: {e}")
            QMessageBox.warning(self, "错误", f"保存历史记录失败: {str(e)}")

    def clear_history(self):
        """清空历史记录"""
        reply = QMessageBox.question(
            self, '确认清空',
            "确定要清空所有历史记录吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            # 清空界面上的记录
            while self.content_layout.count() > 1:  # 保留最后的 stretch
                item = self.content_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            
            # 清空历史记录文件
            try:
                if os.path.exists('cast_history.json'):
                    os.remove('cast_history.json')
                QMessageBox.information(self, "成功", "历史记录已清空")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"清空历史记录失败: {str(e)}")

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, '确认退出',
            "确定要退出应用程序吗？\nDLNA服务器将停止运行。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            # 停止DLNA服务器
            if self.server_running:
                self.stop_server()
            event.accept()
        else:
            event.ignore()

    def load_auto_play_setting(self):
        """加载自动播放设置"""
        try:
            if os.path.exists('settings.json'):
                with open('settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    self.auto_play_checkbox.setChecked(settings.get('auto_play', False))
                    self.auto_download_checkbox.setChecked(settings.get('auto_download', False))
                    self.fullscreen_checkbox.setChecked(settings.get('fullscreen', False))
                    
                    # 加载默认播放器设置
                    default_player = settings.get('default_player', 'mpv')
                    self.mpv_radio.setChecked(default_player == 'mpv')
                    
                    system = platform.system()
                    if system == "Darwin" and hasattr(self, 'iina_radio'):
                        self.iina_radio.setChecked(default_player == 'iina')
                    elif system == "Windows" and hasattr(self, 'potplayer_radio'):
                        self.potplayer_radio.setChecked(default_player == 'potplayer')
        except Exception as e:
            print(f"加载设置失败: {e}")

    def save_auto_play_setting(self):
        """保存自动播放设置"""
        try:
            settings = {}
            if os.path.exists('settings.json'):
                with open('settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            
            settings['auto_play'] = self.auto_play_checkbox.isChecked()
            settings['auto_download'] = self.auto_download_checkbox.isChecked()
            settings['fullscreen'] = self.fullscreen_checkbox.isChecked()
            
            with open('settings.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存设置失败: {e}")
            
    def on_default_player_changed(self, player_type):
        """处理默认播放器选择变化"""
        try:
            # 保存设置
            settings = {}
            if os.path.exists('settings.json'):
                with open('settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            
            settings['default_player'] = player_type
            
            with open('settings.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"更新默认播放器设置失败: {e}")

    def on_auto_play_changed(self, state):
        """处理自动播放复选框状态改变"""
        self.save_auto_play_setting()

    def on_auto_download_changed(self, state):
        """处理自动下载复选框状态改变"""
        self.save_auto_play_setting()

    def on_fullscreen_changed(self, state):
        """处理全屏复选框状态改变"""
        self.save_auto_play_setting()

    def start_server(self):
        """启动DLNA服务器"""
        if self.server_running:
            return
            
        try:
            # 设置MPV渲染器到服务器
            self.dlna_server.mpv_dlna_renderer = self.mpv_dlna_renderer
            
            # 启动服务器
            self.server_thread = threading.Thread(target=self.dlna_server.start, daemon=True)
            self.server_thread.start()
            self.server_running = True
            
            # 更新UI状态
            self.status_icon.setText("🖥️")
            self.status_text.setText("DLNA服务器运行中")
            self.start_server_btn.setVisible(False)
            self.stop_server_btn.setVisible(True)
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"启动服务器失败: {str(e)}")
    
    def stop_server(self):
        """停止DLNA服务器"""
        if not self.server_running:
            return
            
        try:
            # 停止服务器
            self.dlna_server.stop()
            if self.server_thread:
                self.server_thread.join(timeout=1.0)
            self.server_running = False
            
            # 更新UI状态
            self.status_icon.setText("⭕")
            self.status_text.setText("DLNA服务器已停止")
            self.start_server_btn.setVisible(True)
            self.stop_server_btn.setVisible(False)
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"停止服务器失败: {str(e)}")
    
    def update_device_name(self):
        """更新设备名称"""
        new_name = self.device_name_edit.text()
        
        # 禁用按钮，避免重复点击
        self.update_button_state.emit(False, "正在更新...")
        
        def update_name_task():
            try:
                # 停止当前服务器
                if self.server_running:
                    self.dlna_server.stop()
                    if self.server_thread:
                        self.server_thread.join(timeout=1.0)
                
                # 更新名称
                self.dlna_server.name = new_name
                from DLNA.utils import Setting
                Setting.temp_friendly_name = new_name
                
                # 重新启动服务器
                self.server_thread = threading.Thread(target=self.dlna_server.start, daemon=True)
                self.server_thread.start()
                self.server_running = True
                
                # 在主线程中显示成功提示
                QMetaObject.invokeMethod(
                    self,
                    "show_name_update_result",
                    Qt.QueuedConnection,
                    Q_ARG(bool, True),
                    Q_ARG(str, new_name)
                )
            except Exception as e:
                # 在主线程中显示错误提示
                QMetaObject.invokeMethod(
                    self,
                    "show_name_update_result",
                    Qt.QueuedConnection,
                    Q_ARG(bool, False),
                    Q_ARG(str, str(e))
                )
            finally:
                self.update_button_state.emit(True, "修改名称")
        
        # 在后台线程中执行更新操作
        threading.Thread(target=update_name_task, daemon=True).start()
    
    @Slot(bool, str)
    def _update_button_state(self, enabled, text):
        """更新按钮状态"""
        self.update_name_btn.setEnabled(enabled)
        self.update_name_btn.setText(text)
    
    def open_downloads_folder(self):
        """打开下载文件夹"""
        try:
            download_dir = Path("downloads")
            # 如果文件夹不存在则创建
            download_dir.mkdir(exist_ok=True)
            
            if platform.system() == "Darwin":  # macOS
                os.system(f'open "{download_dir}"')
            elif platform.system() == "Windows":  # Windows
                os.startfile(str(download_dir))
        except Exception as e:
            QMessageBox.warning(self, "错误", f"打开下载文件夹失败: {str(e)}")

    def on_mpv_connection_error(self, error_msg):
        """处理MPV连接错误"""
        QMessageBox.warning(self, "错误", f"MPV连接错误: {error_msg}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    # 设置全局变量供mpv_controller.py使用
    import mpv_controller
    mpv_controller.window = window
    window.show()
    sys.exit(app.exec())
