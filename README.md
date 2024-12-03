Hello!

This repository contains an edited version of the megadetector image recognition system. The run_detector_new file is used to sort images based on whether or not they contain an animal, vehicle, or person. It uses a confidence level that you can specify to sort the images. Copies of images at or above the level will be sorted into a output directory that has the same structure as your input directory. All original images will remain in the original folder that you are using. Make sure you have an updated version of megadetector downloaded on your machine. The download can be found here: https://github.com/agentmorris/MegaDetector/blob/main/megadetector.md#downloading-the-model.


Here is an example for setting up the model:


```
if __name__ == '__main__':
#    main()
    #%%
    model_file = "C:\\Users\\bkara\\Downloads\\md_v5a.0.0.pt"
    input_dir = r"C:\\Users\\bkara\\Downloads\\input"
    image_file_names = path_utils.find_images("C:\\Users\\bkara\\Downloads\\input")
    print(image_file_names)
   # image_file_names.append("C:\\Users\f\bkara\\Downloads\\temp\\Large.jpg")
    output_dir = "C:\\Users\\bkara\\Downloads\\output"
    render_confidence_threshold = 0.8
    crop_images = True
    image_file_names = []
    for subdir in os.listdir(input_dir):
        sub_path = os.path.join(input_dir, subdir)
        if os.path.isdir(sub_path):
            image_file_names.extend(path_utils.find_images(sub_path))

        # Ensure the output directory structure matches input structure
        os.makedirs(output_dir, exist_ok=True)


    load_and_run_detector(model_file=model_file,
                          input_dir = input_dir,
                          image_file_names=image_file_names,
                          output_dir=output_dir,
                          render_confidence_threshold=render_confidence_threshold,
                          crop_images=crop_images)
```
This chunk can be found at around line 690 of the run_detector file.

Good Luck!
