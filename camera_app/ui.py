from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk


def fit_geometry(source_width, source_height, canvas_width, canvas_height):
    scale = min(canvas_width/source_width, canvas_height/source_height)
    width, height = max(1, round(source_width*scale)), max(1, round(source_height*scale))
    return scale, (canvas_width-width)/2, (canvas_height-height)/2, width, height


def map_box(box, image_width, scale, x_offset, y_offset, mirror):
    x1, y1, x2, y2 = box
    if mirror:
        x1, x2 = image_width-x2, image_width-x1
    return x_offset+x1*scale, y_offset+y1*scale, x_offset+x2*scale, y_offset+y2*scale


class CameraWindow:
    def __init__(self, root, runtime):
        self.root, self.runtime = root, runtime
        root.title("Camera AI • Giám sát cục bộ")
        root.geometry("1040x780")
        root.minsize(640, 480)
        root.configure(bg="#0d141c")
        root.option_add("*Font", ("Segoe UI", 10))
        self.canvas = tk.Canvas(root, bg="#0d141c", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.photo = None
        self.dialog = None
        self.enroll_dialog = None
        self.last_notice_id = 0
        self.notice_text = ""
        self.notice_until = 0
        self.enroll_saved_id = 0
        self.closing = False
        self.menu = tk.Menu(root, tearoff=False, font=("Segoe UI", 11))
        for label, callback in (("Đăng ký khuôn mặt", self.open_enrollment),
                                ("Danh sách khuôn mặt đã đăng ký", self.open_profiles),
                                ("Chọn người giám sát chính", self.choose_main),
                                ("Căn chỉnh lại hướng đầu/hướng nhìn", self.calibrate)):
            self.menu.add_command(label=label, command=callback)
        self.menu.add_separator()
        self.menu.add_command(label="Thoát", command=self.close)
        self.menu_button = tk.Button(root, text="☰", font=("Segoe UI", 18), command=self.show_menu,
                                     bg="#1d3040", fg="#e3eef7", activebackground="#2c4c60", relief="flat",
                                     bd=0, width=2, cursor="hand2", takefocus=True)
        self.menu_button.place(relx=1, x=-14, y=12, anchor="ne")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Escape>", lambda _: self.close_dialog())
        self.root.after(30, self.refresh)

    def show_menu(self):
        self.menu.tk_popup(self.menu_button.winfo_rootx()-245, self.menu_button.winfo_rooty()+48)

    def _text(self, x, y, text, color="#e3eef7", size=11, bold=False, anchor="nw"):
        return self.canvas.create_text(x, y, text=text, anchor=anchor, fill=color,
                                       font=("Segoe UI", size, "bold" if bold else "normal"), tags="overlay")

    def _label(self, x, y, text, color):
        item = self._text(x+6, max(26, y-26)+3, text, color="#0d141c", bold=True)
        bounds = self.canvas.bbox(item)
        if bounds:
            rect = self.canvas.create_rectangle(bounds[0]-5, bounds[1]-2, bounds[2]+5, bounds[3]+2,
                                                 fill=color, outline="", tags="overlay")
            self.canvas.tag_lower(rect, item)

    def refresh(self):
        if self.closing:
            return
        state = self.runtime.snapshot()
        packet, camera_error = self.runtime.source.snapshot()
        now = time.monotonic()
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        self.canvas.delete("overlay")
        current = packet is not None and now-packet.at <= self.runtime.cfg["result_ttl_seconds"]
        fresh = current and state["at"] is not None and now-state["at"] <= self.runtime.cfg["result_ttl_seconds"]
        mirror = self.runtime.cfg["mirror_preview"] and not self.runtime.source.video
        if current:
            image = cv2.cvtColor(packet.image, cv2.COLOR_BGR2RGB)
            height, width = image.shape[:2]
            scale, ox, oy, dw, dh = fit_geometry(width, height, cw, ch)
            pil = Image.fromarray(image)
            if mirror:
                pil = pil.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            self.photo = ImageTk.PhotoImage(pil.resize((dw, dh), Image.Resampling.BILINEAR))
            self.canvas.itemconfigure(self.image_item, image=self.photo)
            self.canvas.coords(self.image_item, ox, oy)
            if fresh and state.get("image_size") == (width, height):
                objects = state["objects"] if now-state.get("object_at", -1e9) <= self.runtime.cfg["result_ttl_seconds"] else []
                for obj in objects:
                    # Person detections and face detections stay visually distinct.
                    b = map_box(obj["box"], width, scale, ox, oy, mirror)
                    color = "#9ba9bf" if obj["class_id"] == 0 else "#e7b460"
                    self.canvas.create_rectangle(*b, outline=color, width=2, dash=(5, 3), tags="overlay")
                    self._label(b[0], b[1], "VT · " + obj["name"], color)
                for face in state["faces"]:
                    b = map_box(face["box"], width, scale, ox, oy, mirror)
                    color = "#63e6be" if face["primary"] else "#72c7f7"
                    self.canvas.create_rectangle(*b, outline=color, width=3 if face["primary"] else 2, tags="overlay")
                    self._label(b[0], b[1], ("● " if face["primary"] else "Mặt · ")+face["name"][:42], color)
        else:
            self.canvas.itemconfigure(self.image_item, image="")
            self._text(cw/2, ch/2-24, camera_error or "Đang chờ camera…", size=13, anchor="center")
            self._text(cw/2, ch/2+10, "Lỗi nguồn ảnh: tạm dừng các bộ đếm hành vi", color="#9ba9bf", anchor="center")
        top_title = "VIDEO KIỂM THỬ" if self.runtime.source.video else "CAMERA · CPU"
        self.canvas.create_rectangle(0, 0, cw-70, 32, fill="#101f2c", outline="", tags="overlay")
        self._text(14, 6, top_title + ("  ·  Gương" if mirror else ""), color="#aac1d2", size=10, bold=True)
        errors = [(k, v) for k, v in state.get("health", {}).items() if v]
        module_names = {"face_detection": "Phát hiện mặt", "face_verification": "Danh tính",
                        "object_detection": "Vật thể", "head_pose": "Đầu/mắt"}
        alerts = state.get("events", {}).get("alerts", []) if fresh else []
        messages = [a["message"] for a in alerts[:3]]
        if errors:
            messages += [module_names.get(k, k)+": "+v for k, v in errors]
        if state.get("store_error"):
            messages.append(state["store_error"])
        if messages:
            lines = "\n".join(messages[:5])
            self.canvas.create_rectangle(12, 43, min(cw-75, 530), 58+25*len(messages[:5]),
                                         fill="#482c25" if alerts else "#343025", outline="#d3a056", tags="overlay")
            self._text(22, 51, lines, color="#ffe0a3", size=11, bold=True)
        # Compact bottom strip keeps most of the image unobstructed.
        self.canvas.create_rectangle(0, ch-111, cw, ch, fill="#101f2c", outline="", tags="overlay")
        main = state.get("main_name", "Người")
        if not fresh or not state.get("main_visible"):
            main += " · chưa quan sát được"
        self._text(16, ch-102, "Người chính: " + main[:66], size=12, bold=True)
        pose = state.get("pose", {}) if fresh else {}
        self._text(16, ch-75, "Đầu: " + pose.get("head_direction", "Chưa xác định") +
                   "   |   Mắt: " + pose.get("gaze_direction", "Chưa xác định") + " (ước lượng)", size=10)
        events = state.get("events", {})
        away = f"{events.get('head_away_seconds', 0):.1f}".replace(".", ",") if fresh else "—"
        absent = f"{events.get('absence_seconds', 0):.1f}".replace(".", ",") if fresh else "—"
        self._text(16, ch-51, f"Ngoảnh mặt: {away} giây   |   Mất mặt chính: {absent} giây   |   Lượt/60s: {events.get('turn_count', 0)}",
                   color="#b7c9d7", size=10)
        status = state["status"] if fresh or not camera_error else camera_error
        if events.get("suspended"):
            status += " · tạm dừng cảnh báo"
        self._text(16, ch-26, status[:112], color="#e7b460" if events.get("suspended") else "#8ea8bc", size=10)
        if state.get("notice_id", 0) != self.last_notice_id:
            self.last_notice_id = state["notice_id"]
            self.notice_text, self.notice_until = state["notice"], now+6
            if self.enroll_dialog and self.enroll_dialog.winfo_exists():
                self.enroll_message.set(state["notice"])
        if self.notice_text and now < self.notice_until:
            item = self._text(16, ch-137, self.notice_text[:100], color="#ffe0a3", size=10)
            bounds = self.canvas.bbox(item)
            if bounds:
                rect = self.canvas.create_rectangle(bounds[0]-5, bounds[1]-3, bounds[2]+5, bounds[3]+3,
                                                     fill="#343025", outline="", tags="overlay")
                self.canvas.tag_lower(rect, item)
        self._update_enrollment(state)
        self.root.after(40, self.refresh)

    def _new_dialog(self, title, size="500x300"):
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift()
            return None
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry(size)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", self.close_dialog)
        self.dialog = dialog
        return dialog

    def close_dialog(self):
        if self.dialog and self.dialog.winfo_exists():
            if self.dialog == self.enroll_dialog:
                self.runtime.command("enroll_cancel")
                self.enroll_dialog = None
            self.dialog.destroy()
            self.dialog = None

    def open_enrollment(self):
        dialog = self._new_dialog("Đăng ký khuôn mặt", "525x335")
        if dialog is None:
            return
        self.enroll_dialog = dialog
        self.enroll_saved_id = self.runtime.snapshot().get("saved_id", 0)
        self.runtime.command("enroll_open")
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Tên hiển thị", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.enroll_name = tk.StringVar()
        self.enroll_entry = ttk.Entry(body, textvariable=self.enroll_name, width=50)
        self.enroll_entry.pack(fill="x", pady=(6, 10))
        self.enroll_entry.focus_set()
        ttk.Label(body, text="Dùng camera đang mở; chỉ một mặt, đủ sáng và rõ.\nMẫu chỉ nằm trong bộ nhớ cho đến khi bạn bấm Lưu.").pack(anchor="w")
        self.enroll_message = tk.StringVar(value="Nhập tên, rồi bấm Bắt đầu thu mẫu.")
        ttk.Label(body, textvariable=self.enroll_message, wraplength=480).pack(anchor="w", pady=(12, 5))
        self.progress = ttk.Progressbar(body, maximum=self.runtime.cfg["enrollment_samples"])
        self.progress.pack(fill="x", pady=5)
        self.make_main = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="Đặt làm người giám sát chính", variable=self.make_main).pack(anchor="w", pady=5)
        actions = ttk.Frame(body)
        actions.pack(fill="x", side="bottom")
        self.start_samples = ttk.Button(actions, text="Bắt đầu thu mẫu", command=self._start_samples)
        self.start_samples.pack(side="left")
        self.save_samples = ttk.Button(actions, text="Lưu", command=self._save_samples, state="disabled")
        self.save_samples.pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Hủy", command=self.close_dialog).pack(side="right")

    def _start_samples(self):
        name = self.enroll_name.get().strip()
        if not name or len(name) > 100:
            self.enroll_message.set("Nhập tên gồm 1–100 ký tự.")
            return
        self.runtime.command("enroll_start", name=name)
        self.enroll_entry.configure(state="disabled")
        self.start_samples.configure(state="disabled")

    def _save_samples(self):
        self.runtime.command("enroll_save", make_main=self.make_main.get())

    def _update_enrollment(self, state):
        if not self.enroll_dialog or not self.enroll_dialog.winfo_exists():
            return
        if state.get("saved_id", 0) != self.enroll_saved_id:
            self.enroll_dialog.destroy()
            self.enroll_dialog = self.dialog = None
            return
        enrollment = state.get("enrollment")
        if enrollment:
            self.progress["value"] = enrollment["count"]
            self.enroll_message.set(enrollment["message"])
            self.save_samples.configure(state="normal" if enrollment["ready"] else "disabled")
        elif state.get("notice"):
            self.enroll_message.set(state["notice"])

    def open_profiles(self):
        self._profile_dialog(False)

    def choose_main(self):
        self._profile_dialog(True)

    def _profile_dialog(self, choose):
        dialog = self._new_dialog("Chọn người giám sát chính" if choose else "Khuôn mặt đã đăng ký", "560x355")
        if dialog is None:
            return
        body = ttk.Frame(dialog, padding=15)
        body.pack(fill="both", expand=True)
        state = self.runtime.snapshot()
        profiles = state["profiles"]
        ttk.Label(body, text="Chọn hồ sơ trong danh sách" if profiles else "Chưa có hồ sơ. Dùng menu Đăng ký khuôn mặt.").pack(anchor="w")
        listing = tk.Listbox(body, height=9, activestyle="dotbox", exportselection=False)
        listing.pack(fill="both", expand=True, pady=10)
        for p in profiles:
            listing.insert("end", ("● " if p["id"] == state.get("main_profile_id") else "")+p["name"]+"  ·  "+p["created_at"][:10])
        detail = tk.StringVar(value="Hồ sơ mã hóa trên máy; không có kiểm tra người thật/chống ảnh giả.")
        ttk.Label(body, textvariable=detail, wraplength=520).pack(anchor="w", pady=3)

        def selected(action):
            indices = listing.curselection()
            if not indices:
                detail.set("Chọn một hồ sơ trước.")
                return
            p = profiles[indices[0]]
            if action == "delete":
                if not messagebox.askyesno("Xóa hồ sơ", "Xóa hồ sơ “"+p["name"]+"”?", parent=dialog):
                    return
                self.runtime.command("delete", profile_id=p["id"])
                listing.delete(indices[0])
                profiles.pop(indices[0])
                detail.set("Đã yêu cầu xóa hồ sơ.")
            else:
                self.runtime.command("select_main", profile_id=p["id"])
                self.close_dialog()
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(8, 0))
        if choose:
            ttk.Button(actions, text="Chọn người này", command=lambda: selected("select")).pack(side="left")
            def anonymous():
                self.runtime.command("select_main", profile_id=None)
                self.close_dialog()
            ttk.Button(actions, text="Theo dõi tạm “Người”", command=anonymous).pack(side="left", padx=8)
        else:
            ttk.Button(actions, text="Xóa hồ sơ đã chọn", command=lambda: selected("delete")).pack(side="left")
        ttk.Button(actions, text="Đóng", command=self.close_dialog).pack(side="right")

    def calibrate(self):
        self.runtime.command("calibrate")
        dialog = self._new_dialog("Căn chỉnh đầu và mắt", "450x170")
        if dialog:
            body = ttk.Frame(dialog, padding=18)
            body.pack(fill="both", expand=True)
            ttk.Label(body, text="Nhìn gần thẳng vào camera, mở mắt và giữ ổn định.\nTiến độ/hướng dẫn hiện trên khung camera.\nTrong lúc căn chỉnh, cảnh báo hành vi tạm dừng.", wraplength=410).pack(anchor="w")
            ttk.Button(body, text="Trở lại camera", command=self.close_dialog).pack(side="bottom", anchor="e", pady=8)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.menu_button.configure(state="disabled")
        if self.dialog:
            self.dialog.destroy()
        self.runtime.stop()
        self._await_close()

    def _await_close(self):
        if self.runtime.closed():
            # Dispose native image handles on the Tk thread before the interpreter.
            self.canvas.itemconfigure(self.image_item, image="")
            self.photo = None
            self.root.destroy()
        else:
            # Keep Tk responsive while camera/CPU worker releases its resources.
            self.root.after(50, self._await_close)
