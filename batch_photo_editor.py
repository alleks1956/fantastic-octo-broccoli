
        rw, rh = (float(x) for x in text.split(":"))
    except ValueError:
        raise ValueError("Пропорции должны быть в формате «16:9»")
    if rw <= 0 or rh <= 0:
        raise ValueError("Пропорции должны быть положительными")
    return rw / rh


def ratio_crop_size(w, h, ratio):
    """Размер максимальной области с заданным соотношением сторон."""
    if w / h > ratio:
        return max(1, int(h * ratio)), h
    return w, max(1, int(w / ratio))


def crop_size(orig, mode_idx, tw, th, ratio):
    """Размер результата обрезки (без самой обработки картинки)."""
    w, h = orig
    if mode_idx == 0:
        return tw, th
    if mode_idx == 1:
        return ratio_crop_size(w, h, ratio)
    return min(w, tw), min(h, th)


def apply_crop(img, mode_idx, tw, th, ratio):
    w, h = img.size
    if mode_idx == 0:
        return ImageOps.fit(img, (tw, th), method=RESAMPLE, centering=(0.5, 0.5))

    if mode_idx == 1:
        cw, ch = ratio_crop_size(w, h, ratio)
    else:
        cw, ch = min(w, tw), min(h, th)

    left = (w - cw) // 2
    top = (h - ch) // 2
    return img.crop((left, top, left + cw, top + ch))


def normalize_mode(img):
    """Приводит изображение к RGB или RGBA (с сохранением прозрачности)."""
    if img.mode in ("RGB", "RGBA"):
        return img
    has_alpha = img.mode in ("LA", "PA") or img.info.get("transparency") is not None
    return img.convert("RGBA" if has_alpha else "RGB")


def enhance(img, brightness, sharpness):
    """Яркость и чёткость применяются только к цветовым каналам,
    альфа-канал остаётся нетронутым."""
    if abs(brightness - 1.0) < 1e-9 and abs(sharpness - 1.0) < 1e-9:
        return img

    alpha = img.getchannel("A") if img.mode == "RGBA" else None
    rgb = img.convert("RGB")

    if abs(brightness - 1.0) > 1e-9:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if abs(sharpness - 1.0) > 1e-9:
        rgb = ImageEnhance.Sharpness(rgb).enhance(sharpness)

    if alpha is not None:
        rgb.putalpha(alpha)
    return rgb


def transform_image(img, s):
    """Единая функция обработки: используется и в предпросмотре, и в пакете."""
    res = normalize_mode(img)
    if s["crop"]:
        res = apply_crop(res, s["mode"], s["tw"], s["th"], s["ratio"])
    return enhance(res, s["brightness"], s["sharpness"])


def flatten_to_rgb(img, bg=(255, 255, 255)):
    """RGBA -> RGB на однотонном фоне (для JPEG/BMP)."""
    if img.mode == "RGBA":
        base = Image.new("RGB", img.size, bg)
        base.paste(img, mask=img.getchannel("A"))
        return base
    return img.convert("RGB")


def save_image(img, path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        flatten_to_rgb(img).save(path, "JPEG", quality=95, subsampling=0, optimize=True)
    elif ext == ".png":
        img.save(path, "PNG", optimize=True)
    elif ext == ".webp":
        img.save(path, "WEBP", quality=95)
    elif ext == ".bmp":
        flatten_to_rgb(img).save(path, "BMP")
    else:
        img.save(path)


def list_images(folder):
    return sorted(
        (f for f in os.listdir(folder)
         if f.lower().endswith(VALID_EXTS) and os.path.isfile(os.path.join(folder, f))),
        key=str.lower,
    )


# ---------------------------------------------------------------- интерфейс

class BatchPhotoEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("Пакетный редактор фото v3.1 (с предпросмотром)")
        self.root.geometry("960x600")
        self.root.minsize(900, 580)

        self.input_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.aspect_ratio = tk.StringVar(value="1:1")

        self.preview_src = None     # уменьшенная копия первого фото
        self.sample_size = None     # реальный размер первого фото
        self.preview_photo = None   # ссылка на ImageTk (иначе удалит сборщик мусора)

        self._after_id = None       # для debounce предпросмотра
        self.queue = queue.Queue()
        self.is_running = False

        self.create_widgets()

    # ---------- построение окна

    def create_widgets(self):
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        left_col = tk.Frame(main_frame)
        left_col.pack(side="left", fill="y", padx=(0, 10))

        right_col = tk.LabelFrame(main_frame, text="Предпросмотр (1-е фото из папки)")
        right_col.pack(side="right", fill="both", expand=True)
        right_col.pack_propagate(False)
        right_col.bind("<Configure>", lambda e: self.schedule_preview())

        self.preview_label = tk.Label(
            right_col,
            text="Выберите исходную папку,\nчтобы увидеть предпросмотр",
            fg="gray", bg="#EAEAEA",
        )
        self.preview_label.pack(fill="both", expand=True, padx=10, pady=10)

        self.info_label = tk.Label(right_col, text="", fg="#555555", font=("Arial", 9))
        self.info_label.pack(pady=(0, 8))

        # --- 1. Папки ---
        frame_dirs = tk.LabelFrame(left_col, text="1. Папки", padx=10, pady=8)
        frame_dirs.pack(fill="x", pady=(0, 8))

        tk.Button(frame_dirs, text="Выбрать исходную папку",
                  command=self.select_input_dir, width=22).grid(row=0, column=0, sticky="w", pady=2)
        tk.Label(frame_dirs, textvariable=self.input_dir, fg="gray",
                 wraplength=260, justify="left").grid(row=0, column=1, padx=10, sticky="w")

        tk.Button(frame_dirs, text="Папка для сохранения",
                  command=self.select_output_dir, width=22).grid(row=1, column=0, sticky="w", pady=2)
        tk.Label(frame_dirs, textvariable=self.output_dir, fg="gray",
                 wraplength=260, justify="left").grid(row=1, column=1, padx=10, sticky="w")

        # --- 2. Обрезка ---
        frame_crop = tk.LabelFrame(left_col, text="2. Настройки обрезки по центру", padx=10, pady=8)
        frame_crop.pack(fill="x", pady=(0, 8))

        self.crop_var = tk.BooleanVar(value=False)
        tk.Checkbutton(frame_crop, text="Включить обрезку", variable=self.crop_var,
                       command=self.on_setting_change).grid(row=0, column=0, columnspan=4, sticky="w")

        tk.Label(frame_crop, text="Режим:").grid(row=1, column=0, sticky="w", pady=5)
        self.mode_combo = ttk.Combobox(frame_crop, state="readonly", width=35, values=MODES)
        self.mode_combo.current(0)
        self.mode_combo.config(state="disabled")
        self.mode_combo.grid(row=1, column=1, columnspan=3, sticky="w", padx=5)
        self.mode_combo.bind("<<ComboboxSelected>>", lambda e: self.on_setting_change())

        self.params_frame = tk.Frame(frame_crop)
        self.params_frame.grid(row=2, column=0, columnspan=4, sticky="w", pady=5)

        self.lbl_w = tk.Label(self.params_frame, text="Ширина (px):")
        self.width_entry = tk.Entry(self.params_frame, width=8)
        self.width_entry.insert(0, "1080")
        self.width_entry.bind("<KeyRelease>", lambda e: self.schedule_preview())

        self.lbl_h = tk.Label(self.params_frame, text="Высота (px):")
        self.height_entry = tk.Entry(self.params_frame, width=8)
        self.height_entry.insert(0, "1080")
        self.height_entry.bind("<KeyRelease>", lambda e: self.schedule_preview())

        self.lbl_ratio = tk.Label(self.params_frame, text="Пропорции:")
        self.ratio_combo = ttk.Combobox(
            self.params_frame, textvariable=self.aspect_ratio, width=12,
            state="readonly", values=["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
        )
        self.ratio_combo.bind("<<ComboboxSelected>>", lambda e: self.schedule_preview())

        self.lbl_hint = tk.Label(
            frame_crop, fg="gray", font=("Arial", 8), justify="left", anchor="w",
            text="В режиме «Точный вырез» маленькое фото не увеличивается:\n"
                 "результат будет меньше заданного размера.",
        )

        self.update_crop_ui()

        # --- 3. Коррекция ---
        frame_adj = tk.LabelFrame(left_col, text="3. Коррекция (1.0 = оригинал)", padx=10, pady=8)
        frame_adj.pack(fill="x", pady=(0, 8))

        tk.Label(frame_adj, text="Яркость:").grid(row=0, column=0, sticky="w")
        self.brightness_scale = tk.Scale(
            frame_adj, from_=0.2, to=2.0, resolution=0.1, orient="horizontal", length=290,
            command=lambda v: self.schedule_preview())
        tk.Label(frame_adj, text="Четкость:").grid(row=1, column=0, sticky="w")
        self.sharpness_scale = tk.Scale(
            frame_adj, from_=0.0, to=3.0, resolution=0.1, orient="horizontal", length=290,
            command=lambda v: self.schedule_preview())

        # оба слайдера уже созданы, теперь безопасно задавать значения
        self.brightness_scale.set(1.0)
        self.sharpness_scale.set(1.0)
        self.brightness_scale.grid(row=0, column=1, padx=10)
        self.sharpness_scale.grid(row=1, column=1, padx=10)

        # --- Прогресс и запуск ---
        self.progress = ttk.Progressbar(left_col, orient="horizontal", length=460, mode="determinate")
        self.progress.pack(pady=10)

        self.btn_start = tk.Button(
            left_col, text="Начать пакетную обработку", command=self.process_images,
            bg="#4CAF50", fg="white", font=("Arial", 11, "bold"), height=2)
        self.btn_start.pack(fill="x")

    # ---------- состояние интерфейса

    def on_setting_change(self):
        self.update_crop_ui()
        self.schedule_preview()

    def update_crop_ui(self):
        enabled = self.crop_var.get()
        self.mode_combo.config(state="readonly" if enabled else "disabled")

        for widget in self.params_frame.winfo_children():
            widget.grid_forget()
        self.lbl_hint.grid_forget()

        mode_idx = self.mode_combo.current()
        if mode_idx == 1:
            self.lbl_ratio.grid(row=0, column=0, padx=(0, 5))
            self.ratio_combo.config(state="readonly" if enabled else "disabled")
            self.ratio_combo.grid(row=0, column=1)
        else:
            state = "normal" if enabled else "disabled"
            self.lbl_w.grid(row=0, column=0, padx=(0, 5))
            self.width_entry.config(state=state)
            self.width_entry.grid(row=0, column=1, padx=(0, 15))
            self.lbl_h.grid(row=0, column=2, padx=(0, 5))
            self.height_entry.config(state=state)
            self.height_entry.grid(row=0, column=3)
            if mode_idx == 2:
                self.lbl_hint.grid(row=3, column=0, columnspan=4, sticky="w")

    # ---------- выбор папок

    def select_input_dir(self):
        folder = filedialog.askdirectory()
        if folder:
            self.input_dir.set(folder)
            self.load_sample_image(folder)

    def select_output_dir(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_dir.set(folder)

    # ---------- настройки

    def get_settings(self):
        """Снимок настроек. При некорректном вводе бросает ValueError с понятным текстом."""
        crop = self.crop_var.get()
        mode_idx = self.mode_combo.current()
        tw = th = 0
        ratio = 1.0

        if crop:
            if mode_idx == 1:
                ratio = parse_ratio(self.aspect_ratio.get())
            else:
                try:
                    tw = int(self.width_entry.get())
                    th = int(self.height_entry.get())
                except ValueError:
                    raise ValueError("Ширина и высота должны быть целыми числами")
                if not (1 <= tw <= MAX_SIZE and 1 <= th <= MAX_SIZE):
                    raise ValueError(f"Ширина и высота: от 1 до {MAX_SIZE} px")

        return {
            "crop": crop,
            "mode": mode_idx,
            "tw": tw,
            "th": th,
            "ratio": ratio,
            "brightness": float(self.brightness_scale.get()),
            "sharpness": float(self.sharpness_scale.get()),
        }

    # ---------- предпросмотр

    def load_sample_image(self, folder):
        try:
            files = list_images(folder)
        except OSError as e:
            files = []
            self.preview_label.config(image="", text=f"Не удалось прочитать папку:\n{e}")

        if not files:
            self.preview_src = None
            self.sample_size = None
            self.preview_photo = None
            self.preview_label.config(image="", text="В папке нет изображений")
            self.info_label.config(text="", fg="#555555")
            return

        first_path = os.path.join(folder, files[0])
        try:
            with Image.open(first_path) as img:
                img = ImageOps.exif_transpose(img)
                self.sample_size = img.size
                small = normalize_mode(img).copy()
            small.thumbnail((PREVIEW_SRC_MAX, PREVIEW_SRC_MAX), RESAMPLE)
            self.preview_src = small
        except Exception as e:
            self.preview_src = None
            self.sample_size = None
            self.preview_label.config(image="", text=f"Ошибка загрузки:\n{e}")
            return

        self.update_preview()

    def schedule_preview(self):
        """Debounce: пересчитываем предпросмотр, только когда ввод «затих»."""
        if self.preview_src is None:
            return
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
        self._after_id = self.root.after(150, self.update_preview)

    def update_preview(self):
        self._after_id = None
        if self.preview_src is None or self.sample_size is None:
            return

        try:
            s = self.get_settings()
        except ValueError as e:
            self.info_label.config(text=str(e), fg="#B00020")
            return

        # Предпросмотр считаем на уменьшенной копии, размеры пересчитываем пропорционально
        scale = self.preview_src.width / self.sample_size[0]
        ps = dict(s)
        ps["tw"] = max(1, round(s["tw"] * scale))
        ps["th"] = max(1, round(s["th"] * scale))

        try:
            res = transform_image(self.preview_src, ps)

            if res.mode == "RGBA":
                bg = Image.new("RGBA", res.size, PREVIEW_BG + (255,))
                res = Image.alpha_composite(bg, res)
            res = res.convert("RGB")

            box_w = self.preview_label.winfo_width() - 4
            box_h = self.preview_label.winfo_height() - 4
            if box_w < 50 or box_h < 50:
                box_w, box_h = 380, 400
            res.thumbnail((box_w, box_h), RESAMPLE)

            self.preview_photo = ImageTk.PhotoImage(res)
            self.preview_label.config(image=self.preview_photo, text="")
        except Exception as e:
            self.preview_label.config(image="", text=f"Ошибка предпросмотра:\n{e}")
            return

        out = crop_size(self.sample_size, s["mode"], s["tw"], s["th"], s["ratio"]) \
            if s["crop"] else self.sample_size
        self.info_label.config(
            text=f"Оригинал: {self.sample_size[0]}×{self.sample_size[1]}  →  "
                 f"результат: {out[0]}×{out[1]}",
            fg="#555555",
        )

    # ---------- пакетная обработка

    def process_images(self):
        if self.is_running:
            return

        src = self.input_dir.get()
        dst = self.output_dir.get()

        if not src or not os.path.isdir(src):
            messagebox.showerror("Ошибка", "Выберите исходную папку.")
            return
        if not dst:
            messagebox.showerror("Ошибка", "Выберите папку для сохранения.")
            return

        same = os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst))
        if not same and os.path.isdir(dst):
            try:
                same = os.path.samefile(src, dst)
            except OSError:
                pass
        if same:
            messagebox.showerror(
                "Ошибка",
                "Исходная папка и папка сохранения совпадают — оригиналы были бы перезаписаны.\n"
                "Выберите другую папку для сохранения.")
            return

        try:
            settings = self.get_settings()
        except ValueError as e:
            messagebox.showerror("Некорректные настройки", str(e))
            return

        files = list_images(src)
        if not files:
            messagebox.showinfo("Нет файлов", "В исходной папке нет поддерживаемых изображений.")
            return

        try:
            os.makedirs(dst, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось создать папку сохранения:\n{e}")
            return

        conflicts = [f for f in files if os.path.exists(os.path.join(dst, f))]
        if conflicts and not messagebox.askyesno(
                "Файлы уже существуют",
                f"В папке сохранения уже есть файлы с такими же именами ({len(conflicts)} шт.).\n"
                "Перезаписать их?"):
            return

        self.is_running = True
        self.btn_start.config(state="disabled", text="Идёт обработка...")
        self.progress.config(maximum=len(files), value=0)

        threading.Thread(
            target=self.worker, args=(files, src, dst, settings), daemon=True
        ).start()
        self.root.after(100, self.poll_queue)

    def worker(self, files, src, dst, settings):
        """Выполняется в отдельном потоке: к виджетам не обращается."""
        errors = []
        for i, name in enumerate(files, 1):
            try:
                with Image.open(os.path.join(src, name)) as img:
                    img = ImageOps.exif_transpose(img)
                    result = transform_image(img, settings)
                    save_image(result, os.path.join(dst, name))
            except Exception as e:
                errors.append((name, str(e)))
            self.queue.put(("progress", i))
        self.queue.put(("done", len(files), errors))

    def poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == "progress":
                    self.progress["value"] = msg[1]
                else:
                    self.finish_processing(msg[1], msg[2])
                    return
        except queue.Empty:
            pass
        self.root.after(100, self.poll_queue)

    def finish_processing(self, total, errors):
        self.is_running = False
        self.btn_start.config(state="normal", text="Начать пакетную обработку")

        ok = total - len(errors)
        if not errors:
            messagebox.showinfo("Готово", f"Обработано файлов: {ok} из {total}.")
        else:
            details = "\n".join(f"• {n}: {e}" for n, e in errors[:10])
            if len(errors) > 10:
                details += f"\n… и ещё {len(errors) - 10}"
            messagebox.showwarning(
                "Готово с ошибками",
                f"Обработано: {ok} из {total}.\nОшибки ({len(errors)}):\n{details}")


if __name__ == "__main__":
    root = tk.Tk()
    BatchPhotoEditor(root)
    root.mainloop()
