import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'api.dart';

class PaperFigureImage extends StatefulWidget {
  final Api api;
  final String path;
  const PaperFigureImage({super.key, required this.api, required this.path});

  @override
  State<PaperFigureImage> createState() => _PaperFigureImageState();
}

class _PaperFigureImageState extends State<PaperFigureImage> {
  late Future<Uint8List> _image;
  late String _owner;

  void _load() {
    _owner = widget.api.cacheOwner;
    _image = Future.sync(() => widget.api.figureImage(widget.path));
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(PaperFigureImage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.path != widget.path || _owner != widget.api.cacheOwner) {
      _load();
    }
  }

  Widget _failed() => Padding(
        padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 12),
        child: Column(children: [
          const Text('原文图片暂时无法载入', style: TextStyle(color: Colors.black45)),
          TextButton.icon(
              onPressed: () => setState(_load),
              icon: const Icon(Icons.refresh_rounded, size: 18),
              label: const Text('重试图片')),
        ]),
      );

  @override
  Widget build(BuildContext context) => FutureBuilder<Uint8List>(
        future: _image,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const SizedBox(
              height: 180,
              child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
            );
          }
          if (!snapshot.hasData) return _failed();
          final bytes = snapshot.data!;
          return Semantics(
            button: true,
            label: '放大原文图片',
            child: Tooltip(
              message: '点击放大图片',
              child: InkWell(
                onTap: () => showDialog<void>(
                  context: context,
                  builder: (_) => _PaperImagePreview(bytes: bytes),
                ),
                child: Stack(
                  alignment: Alignment.bottomRight,
                  children: [
                    Image.memory(bytes,
                        width: double.infinity,
                        fit: BoxFit.contain,
                        errorBuilder: (_, error, trace) => _failed()),
                    const Padding(
                      padding: EdgeInsets.all(10),
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          color: Color(0xEFFFFFFF),
                          borderRadius: BorderRadius.all(Radius.circular(8)),
                        ),
                        child: Padding(
                          padding: EdgeInsets.all(7),
                          child: Icon(Icons.zoom_in_rounded, size: 22),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      );
}

class _PaperImagePreview extends StatefulWidget {
  final Uint8List bytes;
  const _PaperImagePreview({required this.bytes});
  @override
  State<_PaperImagePreview> createState() => _PaperImagePreviewState();
}

class _PaperImagePreviewState extends State<_PaperImagePreview> {
  final _transform = TransformationController();
  Size _viewport = Size.zero;

  void _zoom(double factor) {
    final scale =
        (_transform.value.getMaxScaleOnAxis() * factor).clamp(1.0, 8.0);
    _transform.value = Matrix4.diagonal3Values(scale, scale, 1)
      ..setTranslationRaw((1 - scale) * _viewport.width / 2,
          (1 - scale) * _viewport.height / 2, 0);
  }

  @override
  void dispose() {
    _transform.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Dialog.fullscreen(
        child: Scaffold(
          appBar: AppBar(
            leading: IconButton(
              tooltip: '关闭图片预览',
              onPressed: () => Navigator.of(context).pop(),
              icon: const Icon(Icons.close_rounded),
            ),
            title: const Text('原文图片'),
          ),
          body: Column(
            children: [
              Expanded(
                child: LayoutBuilder(builder: (context, constraints) {
                  _viewport = constraints.biggest;
                  return InteractiveViewer(
                    key: const ValueKey('paper-image-zoom'),
                    transformationController: _transform,
                    minScale: 1,
                    maxScale: 8,
                    child: Image.memory(
                      widget.bytes,
                      width: constraints.maxWidth,
                      height: constraints.maxHeight,
                      fit: BoxFit.contain,
                      semanticLabel: '原文图片，可放大并拖动查看',
                    ),
                  );
                }),
              ),
              SafeArea(
                top: false,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Wrap(
                        spacing: 12,
                        children: [
                          IconButton(
                            tooltip: '缩小图片',
                            onPressed: () => _zoom(1 / 1.5),
                            icon: const Icon(Icons.zoom_out_rounded),
                          ),
                          IconButton(
                            tooltip: '还原图片大小',
                            onPressed: () =>
                                _transform.value = Matrix4.identity(),
                            icon: const Icon(Icons.fit_screen_rounded),
                          ),
                          IconButton(
                            tooltip: '放大图片',
                            onPressed: () => _zoom(1.5),
                            icon: const Icon(Icons.zoom_in_rounded),
                          ),
                        ],
                      ),
                      const Text('双指或滚轮缩放，拖动查看',
                          textAlign: TextAlign.center,
                          style:
                              TextStyle(fontSize: 12, color: Colors.black54)),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      );
}
